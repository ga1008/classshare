import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_shells.py'], { encoding: 'utf8' }));
const palettes = ['teal','indigo','sky','mint','violet','rose'];
const tokens = JSON.parse(fs.readFileSync('docs/lq-tokens.json','utf8'));
async function mount(page: Page, { live = true, palette = 'indigo', appearance = 'light', script = true } = {}) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-shells.test') return route.abort();
    if (url.pathname.startsWith('/static/js/') || url.pathname === '/static/css/tailwind-app.css') {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/frame') return route.fulfill({ contentType:'text/html',body:'<!doctype html><html lang="zh-CN"><title>独立预览</title><body><input id="frame-draft" aria-label="预览草稿" value="frame原值"><div style="height:1500px">独立纸张</div><script>window.frameIdentity={};parent.postMessage("frame-loaded",location.origin)</script></body></html>' });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="${palette}" data-appearance="${appearance}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ Shells</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}#host{min-width:0}#main-draft{width:100%;min-height:180px}iframe{width:100%;height:250px}.fixture-copy{overflow-wrap:anywhere}</style></head><body><div id="host">${live ? `<form id="outer-form" aria-label="编辑器表单">${fixture.composition}</form>` : '<main id="content"><h1>导航壳预览</h1></main>'}</div><script>window.frameLoads=0;window.addEventListener('message',e=>{if(e.origin===location.origin&&e.data==='frame-loaded')window.frameLoads++})</script>${script ? `<script type="module">import * as api from '/static/js/lq/shells.js';window.api=api;window.errors=[];if(document.querySelector('#live-editor'))window.handle=api.enhanceShell(document.querySelector('#live-editor'),{onError:e=>window.errors.push(String(e))});document.body.dataset.ready='true';</script>` : ''}</body></html>` });
    return route.fulfill({ status:404,body:'local fixture only' });
  });
  await page.goto('https://lq-shells.test/');
  if (script) await expect(page.locator('body')).toHaveAttribute('data-ready','true');
  if (live) await expect(page.frameLocator('#preview-frame').locator('#frame-draft')).toBeAttached();
}

async function mountTopbar(page:Page, {enhance=true}={}) {
  await mount(page,{live:false});
  await page.evaluate(({composition,enhance})=>{
    const w=window as any,form=document.createElement('form');form.id='topbar-form';form.innerHTML=composition;
    document.querySelector('#host')!.prepend(form);w.form=form;w.submitCalls=[];
    form.addEventListener('submit',event=>{event.preventDefault();w.submitCalls.push({owner:(event.target as HTMLFormElement).id,values:[...new FormData(form,(event as SubmitEvent).submitter as HTMLButtonElement).entries()]});});
    w.topbarRoot=form.querySelector('#live-topbar');w.topbarInput=form.querySelector('#topbar-draft');w.topbarButton=form.querySelector('#topbar-submit');w.topbarFrame=form.querySelector('#topbar-frame');w.topbarParent=w.topbarButton.parentNode;
    w.directClicks=0;w.topbarButton.addEventListener('click',()=>w.directClicks++);
    if(enhance)w.topbarHandle=w.api.enhanceShell(w.topbarRoot);
  },{composition:fixture.topbarComposition,enhance});
  await expect(page.frameLocator('#topbar-frame').locator('#frame-draft')).toBeAttached();
  await page.evaluate(()=>{(window as any).frameIdentity=(document.querySelector('#topbar-frame') as HTMLIFrameElement).contentWindow!.frameIdentity;});
}

async function gallery(page:Page, allNavigation=true) {
  await page.evaluate(async ({cases,allNavigation})=>{
    const api=(window as any).api,{createContent}=await import('/static/js/lq/content.js'),{createTable}=await import('/static/js/lq/tables.js'),main=document.querySelector('main')!;
    for(const item of cases){
      if(item.kind!=='page_layout'){
        if(!allNavigation&&!['crumbs','steps','fab'].includes(item.kind))continue;
        const root=api.createShell(item.kind,item.props);if(item.kind==='topbar')document.querySelector('#host')!.prepend(root);else if(item.kind==='dock')document.body.append(root);else main.append(root);if(['topbar','sidebar'].includes(item.kind))api.enhanceShell(root);continue;
      }
      const kind=item.props.kind,head=createContent('page_head',{title:item.props.label,description:'独立呈现示例，业务控制器保持原有所有权。'}),body=document.createElement('p');body.textContent='原有内容、草稿和操作节点放入具名槽。';
      const slots:any={head:[head],main:[createContent('card',{title:'内容区域',meta:'真实 Node 槽'},{body:[body]})]};
      if(kind==='list'){slots.filter=[createContent('filter_bar',{tag:'div',label:'示例筛选',searchId:'layout-search'})];slots.main=[createTable('table',{id:'layout-records',caption:'课程记录示例',columns:[{key:'name',label:'名称',rowHeader:true},{key:'count',label:'次数'}],rows:[{key:'a',cells:{name:'LongUnbrokenName'.repeat(12),count:0}}]})];slots.footer=[createTable('pager',{page:1,totalPages:2})];}
      if(['dashboard','detail'].includes(kind))slots.aside=[createContent('card',{title:'摘要',meta:'辅助区内容由页面提供'})];
      if(kind==='dashboard')slots.main=[createContent('card',{title:'示例计数',variant:'stat',value:0})];
      if(kind==='immersive')slots.aside=[createContent('bubble',{author:'示例教师',time:'10:30',text:'活动面板保持单实例。'})];
      if(kind==='reading')slots.main=[createContent('prose',{text:'阅读正文使用独立排版槽。这里只接已有安全作者 DOM，不生成或注入用户 HTML。'.repeat(3)})];
      main.append(api.createShell('page_layout',item.props,slots));
    }
  },{cases:fixture.cases.filter((x:any)=>['sidebar','crumbs','steps','nav_item','page_layout'].includes(x.kind)||['topbar','dock-nav','fab','fab-small','fab-prominent'].includes(x.props.id)),allNavigation});
}

test.describe('LQ Shells and seven layouts', () => {
  test('real Python/Jinja and JS props/HTML/Element semantic parity and unsafe inputs', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((x:any)=>x.error)).toEqual([]);
    await mount(page,{live:false});
    const result = await page.evaluate(cases => {
      const api=(window as any).api;
      const tree=(n:Node):any=>n.nodeType===3?(n.textContent?.trim()?{text:n.textContent}:null):{tag:(n as Element).tagName,attrs:Object.fromEntries([...(n as Element).attributes].map(a=>[a.name,a.value]).sort()),children:[...n.childNodes].map(tree).filter(Boolean)};
      const parse=(html:string)=>{const t=document.createElement('template');t.innerHTML=html;return [...t.content.childNodes].map(tree).filter(Boolean);};
      return cases.map((item:any)=>({props:api.shellProps(item.kind,item.props),jinja:parse(item.html),html:parse(api.html[item.kind](item.props)),element:[tree(api.createShell(item.kind,item.props))]}));
    },fixture.cases);
    result.forEach((r:any,i:number)=>{expect(r.props).toEqual(fixture.cases[i].normalized);expect(r.html).toEqual(r.jinja);expect(r.element).toEqual(r.jinja);});
    expect(fixture.invalid.every((x:any)=>x.error==='ValueError')).toBe(true);
    const invalid=await page.evaluate(cases=>cases.map((item:any)=>['shellProps','shellMarkup','createShell'].map(method=>{try{(window as any).api[method](item.kind,item.props);return false;}catch(e){return e instanceof TypeError;}})),fixture.invalid);
    invalid.forEach((r:boolean[])=>expect(r).toEqual([true,true,true]));
  });

  test('SSR without enhancement keeps auxiliary content available and native details work',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page,{script:false});
    await expect(page.locator('#rail-draft')).toBeVisible();await expect(page.locator('#preview-frame')).toBeVisible();
    await page.locator('#rail-draft').fill('无JS可编辑');await expect(page.locator('#rail-draft')).toHaveValue('无JS可编辑');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
  });

  test('Jinja caller slots equal authored HTML and Element Node slots without raw HTML API',async({page})=>{
    await mount(page,{live:false});
    const result=await page.evaluate(composition=>{
      const api=(window as any).api,source=document.createElement('div');source.innerHTML=composition;
      const props={id:'live-editor',title:'保留原位的编辑器',primary:{key:'save',label:'保存'}};
      const slots=Object.fromEntries(['main','rail','aside'].map(name=>[name,source.querySelector(`[data-lq-slot="${name}"]`)!.innerHTML]));
      const nodes=()=>Object.fromEntries(Object.entries(slots).map(([name,html])=>{const div=document.createElement('div');div.innerHTML=html;return [name,[...div.childNodes]];}));
      const html=document.createElement('div');html.innerHTML=api.html.editor(props);for(const [name,children] of Object.entries(nodes()))html.querySelector(`[data-lq-slot="${name}"]`)!.append(...children);
      const element=api.createShell('editor',props,nodes());
      const tree=(n:Node):any=>n.nodeType===3?(n.textContent?.trim()?{text:n.textContent}:null):{tag:(n as Element).tagName,attrs:Object.fromEntries([...(n as Element).attributes].map(a=>[a.name,a.value]).sort()),children:[...n.childNodes].map(tree).filter(Boolean)};
      return {jinja:tree(source.firstElementChild!),html:tree(html.firstElementChild!),element:tree(element)};
    },fixture.composition);
    expect(result.html).toEqual(result.jinja);expect(result.element).toEqual(result.jinja);
  });

  test('all slots validate before movement, reject external form/fieldset/live frame and retain native nodes',async({page})=>{
    await mount(page,{live:false});
    const result=await page.evaluate(()=>{
      const api=(window as any).api, source=document.createElement('form');source.id='source';source.innerHTML='<input id="draft" name="draft" value="original"><fieldset disabled><input id="locked" name="locked"></fieldset><iframe title="live" src="/frame"></iframe>';document.querySelector('main')!.append(source);
      const input=source.querySelector('input')!, locked=source.querySelector('#locked')!, frame=source.querySelector('iframe')!;
      const p={id:'slots',title:'槽'};let rejected=0;
      for(const slots of [{main:[input],wrong:[]},{main:[input],aside:['bad']},{main:[input]},{main:[locked]},{aside:[frame]},{main:[source],aside:[input]}])try{api.createShell('editor',p,slots);}catch{rejected++;}
      const untouched=input.parentElement===source&&frame.parentElement===source;let events=0;input.addEventListener('input',()=>events++);input.value='草稿仍在';
      frame.remove();const created=api.createShell('editor',p,{main:[source]});document.querySelector('main')!.append(created);input.dispatchEvent(new Event('input'));
      return {rejected,untouched,same:created.querySelector('#draft')===input,form:input.form===source,value:input.value,events};
    });
    expect(result).toEqual({rejected:6,untouched:true,same:true,form:true,value:'草稿仍在',events:1});
  });

  test('in-place drawer preserves form owner, disabled fieldset, FormData, input/files and iframe session',async({page})=>{
    await mount(page);await page.locator('#rail-draft').fill('未保存修改');await page.locator('#file-draft').setInputFiles({name:'draft.txt',mimeType:'text/plain',buffer:Buffer.from('draft')});
    await page.frameLocator('#preview-frame').locator('#frame-draft').fill('iframe草稿');
    await page.evaluate(()=>{const w=window as any,p=document.querySelector('#live-editor--lq-rail')!,f=document.querySelector('#preview-frame') as HTMLIFrameElement;w.snapshot={pane:p,parent:p.parentNode,next:p.nextSibling,input:document.querySelector('#rail-draft'),frame:f,doc:f.contentDocument,identity:(f.contentWindow as any).frameIdentity,loads:w.frameLoads};w.delegated=0;document.querySelector('#outer-form')!.addEventListener('input',()=>w.delegated++);});
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>(window as any).handle.openPane('rail'));
    await expect(page.locator('#rail-draft')).toBeVisible();await page.locator('#rail-draft').fill('drawer内修改');
    await expect(page.locator('#disabled-draft')).toBeDisabled();
    const data=await page.evaluate(()=>{const w=window as any,s=w.snapshot,input=document.querySelector('#rail-draft') as HTMLInputElement,form=document.querySelector('#outer-form') as HTMLFormElement;return {same:input===s.input,parent:s.pane.parentNode===s.parent,next:s.pane.nextSibling===s.next,form:input.form===form,values:[...new FormData(form).entries()].map(([k,v])=>[k,typeof v==='string'?v:v.name]),file:(document.querySelector('#file-draft') as HTMLInputElement).files![0].name,delegated:w.delegated};});
    expect(data).toEqual({same:true,parent:true,next:true,form:true,values:[['title','drawer内修改'],['file','draft.txt'],['mainDraft','正文草稿']],file:'draft.txt',delegated:1});
    await page.keyboard.press('Escape');await expect(page.locator('#live-editor--lq-rail')).toBeHidden();
    await page.evaluate(()=>(window as any).handle.openPane('aside'));
    await expect(page.locator('#preview-frame')).toBeVisible();await expect(page.frameLocator('#preview-frame').locator('#frame-draft')).toHaveValue('iframe草稿');
    await page.setViewportSize({width:1440,height:980});await expect(page.locator('#preview-frame')).toBeVisible();
    expect(await page.evaluate(()=>{const w=window as any,s=w.snapshot,f=document.querySelector('#preview-frame') as HTMLIFrameElement;return {same:f===s.frame,doc:f.contentDocument===s.doc,identity:(f.contentWindow as any).frameIdentity===s.identity,loads:w.frameLoads===s.loads};})).toEqual({same:true,doc:true,identity:true,loads:true});
  });

  test('resize, focus selection, guarded content and duplicate mount/destroy remain bounded',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page);
    await page.evaluate(()=>(window as any).handle.openPane('rail'));await page.locator('#rail-draft').focus();
    await page.evaluate(()=>{(document.querySelector('#rail-draft') as HTMLInputElement).setSelectionRange(1,4);});
    await page.setViewportSize({width:1440,height:980});await expect(page.locator('#live-editor--lq-rail')).toHaveAttribute('data-lq-pane-mode','inline');await expect(page.locator('#rail-draft')).toBeFocused();
    expect(await page.locator('#rail-draft').evaluate((e:HTMLInputElement)=>[e.selectionStart,e.selectionEnd])).toEqual([1,4]);
    await page.setViewportSize({width:390,height:844});
    await expect(page.locator('.lq-editor__mobile [data-lq-pane-open="rail"]')).toBeFocused();
    await page.evaluate(()=>(window as any).handle.refresh({rail:{dirty:true},aside:{hasError:true}}));
    await expect(page.locator('#rail-draft')).toBeVisible();await expect(page.locator('#preview-frame')).toBeVisible();
    const result=await page.evaluate(async()=>{
      const w=window as any,root=document.querySelector('#live-editor')!,first=w.handle,duplicate=await import('/static/js/lq/shells.js?duplicate');const same=duplicate.enhanceShell(root)===first;
      first.destroy();first.destroy();const restored=!root.hasAttribute('data-lq-enhanced')&&!root.querySelector('[data-lq-pane][hidden]');
      w.handle=duplicate.enhanceShell(root);w.handle.openPane('rail');root.remove();
      return {same,restored};
    });
    expect(result).toEqual({same:true,restored:true});
    await expect.poll(()=>page.evaluate(()=>document.querySelector('#live-editor')===null&&document.body.style.overflow!=='hidden')).toBe(true);
  });

  test('unsafe transform/clip/stacking/containment and incompatible native top layer reject before opening',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page);
    const result=await page.evaluate(()=>{
      const w=window as any,host=document.querySelector('#host') as HTMLElement,pane=document.querySelector('#live-editor--lq-rail')!,parent=pane.parentNode,results=[];
      for(const css of ['transform:translateX(0)','overflow:hidden','position:relative;z-index:1','isolation:isolate','contain:paint','opacity:.99','filter:blur(0px)']){host.style.cssText=css;try{w.handle.openPane('rail');results.push(false);}catch{results.push(pane.hidden&&pane.parentNode===parent);}}
      host.removeAttribute('style');const dialog=document.createElement('dialog');dialog.textContent='原生模态';document.body.append(dialog);dialog.showModal();let top=false;try{w.handle.openPane('rail');}catch{top=true;}dialog.close();dialog.remove();return {results,top};
    });
    expect(result).toEqual({results:Array(7).fill(true),top:true});
    await page.evaluate(()=>(window as any).handle.openPane('rail'));await expect(page.locator('#rail-draft')).toBeVisible();
    const hit=await page.locator('#rail-draft').evaluate(el=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;});expect(hit).toBe(true);
  });

  test('an unclipped native modal host keeps the connected pane in the top layer',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page,{live:false});
    const result=await page.evaluate(()=>{
      const api=(window as any).api,dialog=document.createElement('dialog');dialog.style.overflow='visible';dialog.setAttribute('aria-label','父模态');document.body.append(dialog);dialog.showModal();
      const input=document.createElement('input');input.setAttribute('aria-label','模态内草稿');input.value='draft';
      const root=api.createShell('editor',{id:'modal-editor',title:'父模态中的编辑器'},{rail:[input]});dialog.append(root);
      const pane=root.querySelector('[data-lq-pane="rail"]')!,parent=pane.parentNode,handle=api.enhanceShell(root);handle.openPane('rail');input.focus();
      const rect=input.getBoundingClientRect(),hit=document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2)===input;
      const state={same:pane.parentNode===parent,hit,focused:document.activeElement===input,value:input.value};dialog.close();handle.destroy();dialog.remove();return state;
    });
    expect(result).toEqual({same:true,hit:true,focused:true,value:'draft'});
  });

  test('topbar measures real sticky height, condenses and restores owned state on destroy',async({page})=>{
    await mount(page,{live:false});
    await page.evaluate(item=>{const w=window as any,root=w.api.createShell(item.kind,item.props);root.style.setProperty('--lq-topbar-h','99px');document.body.prepend(root);document.querySelector('main')!.style.minHeight='2000px';w.topbar=w.api.enhanceShell(root);},fixture.cases[0]);
    await page.evaluate(()=>scrollTo(0,100));await expect(page.locator('#topbar')).toHaveAttribute('data-lq-condensed','true');
    await expect.poll(()=>page.locator('#topbar').evaluate(el=>Math.abs(parseFloat((el as HTMLElement).style.getPropertyValue('--lq-topbar-h'))-el.getBoundingClientRect().height)<1)).toBe(true);
    expect(await page.locator('#topbar').evaluate(el=>el.getBoundingClientRect().top)).toBe(0);
    await page.evaluate(()=>(window as any).topbar.destroy());await expect(page.locator('#topbar')).not.toHaveAttribute('data-lq-condensed');
    expect(await page.locator('#topbar').evaluate(el=>(el as HTMLElement).style.getPropertyValue('--lq-topbar-h'))).toBe('99px');
  });

  for(const width of [320,375,390,767,768,769,1023,1024,1025,1279,1280,1281]) test(`editor breakpoint ${width} preserves main width and control reachability`,async({page})=>{
    await page.setViewportSize({width,height:980});await mount(page);
    await expect(page.locator('#live-editor--lq-rail')).toHaveAttribute('data-lq-pane-mode',width<1024?'drawer':'inline');
    await expect(page.locator('#live-editor--lq-aside')).toHaveAttribute('data-lq-pane-mode',width<1280?'drawer':'inline');
    expect(await page.locator('#live-editor--lq-main').evaluate(el=>el.getBoundingClientRect().width)).toBeGreaterThanOrEqual(320);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(width<768) await expect(page.locator('.lq-editor__mobile [data-lq-command="save"]')).toBeVisible();
  });

  test('sidebar single group/search/current/scoped preference and native keyboard are reversible',async({page})=>{
    await mount(page,{live:false});
    await page.evaluate(item=>{const w=window as any,root=w.api.createShell(item.kind,item.props);document.querySelector('main')!.append(root);w.sidebar=w.api.enhanceShell(root);},fixture.cases.find((x:any)=>x.kind==='sidebar'));
    const search=page.locator('#sidebar input[type=search]');await search.fill('材料');await expect(page.locator('#sidebar a[href="/files"]')).toBeVisible();await expect(page.locator('#sidebar a[href="/courses"]')).toBeHidden();
    await search.fill('');await expect(page.locator('#sidebar [aria-current="page"]')).toBeVisible();
    await page.locator('#sidebar [data-lq-nav-group="archive"] summary').focus();await page.keyboard.press('Enter');
    await expect(page.locator('#sidebar [data-lq-nav-group="teaching"]')).not.toHaveAttribute('open');
    expect(await page.evaluate(()=>localStorage.getItem('lq.sidebar:["teacher:1","manage","navigation"]'))).toBe('archive');
    await page.setViewportSize({width:1024,height:980});await page.locator('h1').click();await page.keyboard.press('Control+k');await expect(search).toBeFocused();
    await page.evaluate(()=>(window as any).sidebar.destroy());
    await expect(page.locator('#sidebar [data-lq-nav-group="teaching"]')).toHaveAttribute('open');
  });

  test('sidebar queued search restoration cannot override the next native group choice',async({page})=>{
    await mount(page,{live:false});
    await page.evaluate(item=>{const w=window as any,root=w.api.createShell(item.kind,item.props);document.querySelector('main')!.append(root);w.sidebar=w.api.enhanceShell(root);},fixture.cases.find((x:any)=>x.kind==='sidebar'));
    await page.locator('#sidebar input[type=search]').fill('材料');
    await expect(page.locator('#sidebar a[href="/files"]')).toBeVisible();
    // Queue the restored teaching group's toggle and the user's archive choice
    // in one task, before either native toggle event has been delivered.
    await page.evaluate(()=>{const root=document.querySelector('#sidebar')!,search=root.querySelector('input')!;search.value='';search.dispatchEvent(new Event('input',{bubbles:true}));(root.querySelector('[data-lq-nav-group="archive"] summary') as HTMLElement).click();});
    await expect(page.locator('#sidebar [data-lq-nav-group="teaching"]')).not.toHaveAttribute('open');
    await expect(page.locator('#sidebar [data-lq-nav-group="archive"]')).toHaveAttribute('open');
    await expect.poll(()=>page.evaluate(()=>localStorage.getItem('lq.sidebar:["teacher:1","manage","navigation"]'))).toBe('archive');
    await page.locator('#sidebar input[type=search]').fill('课堂');await page.locator('#sidebar input[type=search]').fill('');
    await expect(page.locator('#sidebar a[href="/files"]')).toBeVisible();
    await expect(page.locator('#sidebar [data-lq-nav-group="teaching"]')).not.toHaveAttribute('open');
    await page.evaluate(()=>(window as any).sidebar.destroy());
    await expect(page.locator('#sidebar [data-lq-nav-group="teaching"]')).toHaveAttribute('open');
    await expect(page.locator('#sidebar [data-lq-nav-group="archive"]')).not.toHaveAttribute('open');
  });

  test('Dock keyboard detection requires a live equivalent command and releases shared subscriptions',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page,{live:false});
    const result=await page.evaluate(item=>{
      const w=window as any,api=w.api,content=document.querySelector('main') as HTMLElement;
      const input=document.createElement('input');input.setAttribute('aria-label','草稿');const fallback=document.createElement('button');fallback.dataset.lqCommand='submit';fallback.textContent='提交';content.append(input,fallback);
      const viewport=new EventTarget() as any;viewport.height=844;viewport.scale=1;Object.defineProperty(window,'visualViewport',{configurable:true,value:viewport});
      const root=api.createShell(item.kind,item.props);document.body.append(root);const handle=api.enhanceDock(root,{contentRoot:content,fallbacks:{submit:fallback}});const duplicate=api.enhanceDock(root,{contentRoot:content})===handle;
      input.focus();viewport.height=600;viewport.dispatchEvent(new Event('resize'));const hidden=root.hidden&&root.inert;
      fallback.hidden=true;handle.refresh();const invalidFallbackVisible=!root.hidden;fallback.hidden=false;
      viewport.scale=2;handle.refresh();const zoomVisible=!root.hidden;viewport.scale=1;viewport.height=800;handle.refresh();const chromeVisible=!root.hidden;
      viewport.height=600;handle.refresh();input.blur();handle.refresh();const unfocusedVisible=!root.hidden;
      const subscribers=w[Symbol.for('lanshare.lq.viewport')].subscribers.size;handle.destroy();handle.destroy();
      return {duplicate,hidden,invalidFallbackVisible,zoomVisible,chromeVisible,unfocusedVisible,subscribers,clean:!w[Symbol.for('lanshare.lq.viewport')]&&!content.hasAttribute('data-lq-dock-content')&&!root.hidden&&!root.inert};
    },fixture.cases.find((x:any)=>x.props.id==='dock-actions'));
    expect(result).toEqual({duplicate:true,hidden:true,invalidFallbackVisible:true,zoomVisible:true,chromeVisible:true,unfocusedVisible:true,subscribers:1,clean:true});
  });

  test('Dock focus changes avoid synchronous geometry reads while resize and refresh still compensate',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page,{live:false});
    const result=await page.evaluate(async item=>{
      const w=window as any,content=document.querySelector('main') as HTMLElement;
      const first=document.createElement('input'),second=document.createElement('input');
      const fallback=document.createElement('button');fallback.dataset.lqCommand='submit';fallback.textContent='提交';
      content.append(first,second,fallback);
      const root=w.api.createShell(item.kind,item.props);document.body.append(root);
      const handle=w.api.enhanceDock(root,{contentRoot:content,fallbacks:{submit:fallback}});
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      let reads=0;const readRect=root.getBoundingClientRect;
      root.getBoundingClientRect=function(){reads++;return readRect.call(this);};
      for(let index=0;index<20;index++){first.focus();second.focus();}
      const focusReads=reads;
      window.dispatchEvent(new Event('resize'));const resizeMeasured=reads>focusReads;
      const beforeRefresh=reads;handle.refresh();const refreshMeasured=reads>beforeRefresh;
      root.style.height='120px';
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const compensated=content.style.getPropertyValue('--lq-dock-h')===`${readRect.call(root).height+24}px`;
      handle.destroy();return {focusReads,resizeMeasured,refreshMeasured,compensated};
    },fixture.cases.find((x:any)=>x.props.id==='dock-actions'));
    expect(result).toEqual({focusReads:0,resizeMeasured:true,refreshMeasured:true,compensated:true});
  });

  test('Dock more reuses one real sheet and returns original commands on close and disposal',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mount(page,{live:false});
    await page.evaluate(item=>{const w=window as any,root=w.api.createShell(item.kind,item.props);document.body.append(root);w.moreOriginal=root.querySelector('[data-lq-dock-overflow]');w.moreParent=w.moreOriginal.parentNode;w.dock=w.api.enhanceDock(root,{contentRoot:document.querySelector('main')});},fixture.cases.find((x:any)=>x.props.id==='dock-more'));
    await page.locator('#dock-more summary').focus();await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog',{name:'更多操作'})).toBeVisible();
    await expect(page.getByRole('dialog').getByRole('link',{name:'导出'})).toHaveAttribute('href','/export');
    await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('#dock-more summary')).toBeFocused();
    expect(await page.evaluate(()=>(window as any).moreOriginal.parentNode===(window as any).moreParent)).toBe(true);
    await page.evaluate(async()=>{const w=window as any;await w.dock.openMore();w.dock.destroy();});
    await expect(page.getByRole('dialog')).toHaveCount(0);expect(await page.evaluate(()=>(window as any).moreOriginal.parentNode===(window as any).moreParent)).toBe(true);
    expect(await page.evaluate(async()=>{const w=window as any,h=w.api.enhanceDock(document.querySelector('#dock-more'),{contentRoot:document.querySelector('main')});const pending=h.openMore();h.destroy();return await pending===null;})).toBe(true);
  });

  test('seven layouts and named topbar/actions fit coarse mobile with forced/reduced preferences',async({browser})=>{
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});try{
      const page=await context.newPage();await mount(page,{live:false});
      await gallery(page,false);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
      for(const size of await page.locator('.lq-nav-item,.lq-fab').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>el.getBoundingClientRect().height)))expect(size).toBeGreaterThanOrEqual(44);
      await page.screenshot({path:'.codex-temp/lq-shells-seven-mobile.png',fullPage:true});
      await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});await page.locator('#fab').focus();await expect(page.locator('#fab')).toHaveCSS('outline-style','solid');
    }finally{await context.close();}
  });

  test('200 percent CSS zoom and late close after breakpoint retain draft and visible main',async({page})=>{
    await page.setViewportSize({width:640,height:980});await mount(page);
    await page.evaluate(()=>{document.documentElement.style.zoom='2';});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.evaluate(async()=>{const w=window as any;w.handle.openPane('rail');w.pendingClose=w.handle.closePane();});
    await page.setViewportSize({width:1440,height:980});
    await page.evaluate(async()=>{document.documentElement.style.zoom='';await (window as any).pendingClose;});
    await expect(page.locator('#rail-draft')).toBeVisible();await expect(page.locator('#rail-draft')).toHaveValue('未保存标题');
    await expect(page.locator('#live-editor--lq-rail')).toHaveAttribute('data-lq-pane-mode','inline');
  });


  test('topbar mobile more keeps real native form controls, fieldset and iframe in place',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mountTopbar(page);
    await expect(page.locator('#topbar-submit')).toBeHidden();await expect(page.locator('#live-topbar > [data-lq-pane-open]')).toBeVisible();
    await page.locator('#live-topbar > [data-lq-pane-open]').focus();await page.keyboard.press('Enter');
    await expect(page.locator('#live-topbar--lq-actions')).toHaveAttribute('aria-modal','true');
    expect(await page.locator('#live-topbar--lq-actions').evaluate(el=>el.matches(':modal'))).toBe(true);
    await page.locator('#topbar-draft').fill('保留的新草稿');await expect(page.locator('#topbar-locked')).toBeDisabled();
    await page.frameLocator('#topbar-frame').locator('#frame-draft').fill('iframe草稿');
    await page.locator('#topbar-submit').click();
    expect(await page.evaluate(()=>(window as any).submitCalls)).toEqual([{owner:'topbar-form',values:[['draft','保留的新草稿'],['intent','save']]}]);
    expect(await page.evaluate(()=>(window as any).directClicks)).toBe(1);
    await page.locator('#topbar-draft').focus();await page.keyboard.press('Escape');
    await expect(page.locator('#topbar-submit')).toBeHidden();await expect(page.locator('#live-topbar > [data-lq-pane-open]')).toBeFocused();
    await page.setViewportSize({width:1024,height:980});await expect(page.locator('#topbar-submit')).toBeVisible();
    expect(await page.evaluate(()=>{const w=window as any;return w.topbarButton===document.querySelector('#topbar-submit')&&w.topbarButton.parentNode===w.topbarParent&&w.topbarButton.form===w.form&&w.topbarFrame.contentWindow.frameIdentity===w.frameIdentity&&w.frameLoads===1;})).toBe(true);
    await expect(page.frameLocator('#topbar-frame').locator('#frame-draft')).toHaveValue('iframe草稿');
    await page.locator('#topbar-draft').focus();await page.setViewportSize({width:1023,height:980});await expect(page.locator('#live-topbar > [data-lq-pane-open]')).toBeFocused();
    await page.evaluate(()=>(window as any).topbarHandle.destroy());await expect(page.locator('#topbar-submit')).toBeVisible();await expect(page.locator('#topbar-draft')).toHaveValue('保留的新草稿');
    await expect(page.locator('#live-topbar--lq-actions')).not.toHaveAttribute('aria-modal');
  });

  test('topbar native top layer clears sticky clipping while preserving DOM and supports breakpoint close',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mountTopbar(page);
    await page.evaluate(()=>{const host=document.querySelector('#host') as HTMLElement;host.style.cssText='transform:translateX(0);overflow:hidden;contain:paint;isolation:isolate;position:relative;z-index:1;max-height:80px';(window as any).topbarHandle.openPane('actions');});
    const hit=await page.locator('#topbar-submit').evaluate(el=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;});expect(hit).toBe(true);
    expect(await page.locator('#live-topbar').evaluate(el=>el.classList.contains('lq-glass'))).toBe(false);
    await page.locator('#topbar-draft').focus();await page.evaluate(()=>{(document.querySelector('#topbar-draft') as HTMLInputElement).setSelectionRange(0,2);});
    await page.setViewportSize({width:1024,height:980});
    await expect(page.locator('#live-topbar--lq-actions')).toHaveAttribute('data-lq-pane-mode','inline');
    expect(await page.locator('#live-topbar--lq-actions').evaluate(el=>el.matches(':modal'))).toBe(false);
    await expect(page.locator('#topbar-draft')).toBeFocused();expect(await page.locator('#topbar-draft').evaluate((el:HTMLInputElement)=>[el.selectionStart,el.selectionEnd])).toEqual([0,2]);
    await page.evaluate(()=>{(document.querySelector('#host') as HTMLElement).removeAttribute('style');});
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>(window as any).topbarHandle.openPane('actions'));
    await page.evaluate(()=>document.querySelector('#live-topbar')!.remove());
    await expect.poll(()=>page.evaluate(()=>!document.querySelector('dialog:modal')&&document.body.style.overflow!=='hidden')).toBe(true);
  });

  test('topbar no enhancement and missing native dialog support retain accessible actions',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mountTopbar(page,{enhance:false});
    await expect(page.locator('#topbar-submit')).toBeVisible();await page.locator('#topbar-submit').click();
    expect(await page.evaluate(()=>(window as any).submitCalls.length)).toBe(1);
    await page.evaluate(()=>{const w=window as any,panel=document.querySelector('#live-topbar--lq-actions')!;Object.defineProperty(panel,'showModal',{value:undefined,configurable:true});w.topbarHandle=w.api.enhanceShell(w.topbarRoot);});
    await expect(page.locator('#topbar-submit')).toBeVisible();await expect(page.locator('#live-topbar > [data-lq-pane-open]')).toBeHidden();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
  });

  test('topbar refresh scroll and resize snapshot viewport reads before material writes',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mountTopbar(page);
    const results=await page.evaluate(()=>{
      const w=window as any,root=w.topbarRoot as HTMLElement,events:string[]=[],restores:(()=>void)[]=[];
      let recording=false;
      for(const key of ['innerWidth','scrollY']){
        const descriptor=Object.getOwnPropertyDescriptor(window,key)!;
        Object.defineProperty(window,key,{...descriptor,get(){if(recording)events.push(`read:${key}`);return Reflect.apply(descriptor.get!,this,[]);}});
        restores.push(()=>Object.defineProperty(window,key,descriptor));
      }
      const set=Element.prototype.setAttribute,toggle=DOMTokenList.prototype.toggle;
      Element.prototype.setAttribute=function(name,value){if(recording&&(this===root||root.contains(this)))events.push(`write:${name}`);return Reflect.apply(set,this,[name,value]);};
      DOMTokenList.prototype.toggle=function(...args:Parameters<DOMTokenList['toggle']>){if(recording&&this===root.classList)events.push('write:class');return Reflect.apply(toggle,this,args);};
      try{
        return ['refresh','scroll','resize'].map(source=>{
          root.classList.remove('lq-glass');root.removeAttribute('data-lq-condensed');root.removeAttribute('data-lq-scroll-edge');events.length=0;recording=true;
          try{if(source==='refresh')w.topbarHandle.refresh();else window.dispatchEvent(new Event(source));}finally{recording=false;}
          return {source,events:[...events],glass:root.classList.contains('lq-glass'),condensed:root.dataset.lqCondensed,edge:root.dataset.lqScrollEdge};
        });
      }finally{Element.prototype.setAttribute=set;DOMTokenList.prototype.toggle=toggle;restores.reverse().forEach(restore=>restore());}
    });
    for(const result of results){
      expect(result.events.slice(0,2)).toEqual(['read:innerWidth','read:scrollY']);
      expect(result.events.filter(event=>event.startsWith('read:'))).toEqual(['read:innerWidth','read:scrollY']);
      expect(result.events.slice(2).some(event=>event.startsWith('write:'))).toBe(true);
      expect({glass:result.glass,condensed:result.condensed,edge:result.edge}).toEqual({glass:true,condensed:'false',edge:'false'});
    }
  });

  test('topbar scroll edge is distinct from condensation and view transition name is explicitly unique',async({page})=>{
    await mount(page,{live:false});
    await page.evaluate(()=>{const w=window as any;w.bar=w.api.createShell('topbar',{id:'edge',title:'顶栏',viewTransition:true,primary:{key:'save',label:'保存'}});document.body.prepend(w.bar);document.querySelector('main')!.style.minHeight='2200px';w.edge=w.api.enhanceShell(w.bar);});
    await expect(page.locator('#edge')).toHaveCSS('view-transition-name','lq-topbar');await expect(page.locator('#edge')).toHaveAttribute('data-lq-scroll-edge','false');
    await page.evaluate(()=>scrollTo(0,40));await expect(page.locator('#edge')).toHaveAttribute('data-lq-scroll-edge','true');await expect(page.locator('#edge')).toHaveAttribute('data-lq-condensed','false');
    expect(await page.locator('#edge').evaluate(el=>getComputedStyle(el,'::after').opacity)).toBe('1');
    await page.evaluate(()=>scrollTo(0,90));await expect(page.locator('#edge')).toHaveAttribute('data-lq-condensed','true');
    expect(await page.evaluate(()=>{const w=window as any,second=w.api.createShell('topbar',{id:'second',title:'第二个',viewTransition:true});document.body.append(second);try{w.api.enhanceShell(second);return false;}catch(e){second.remove();return e instanceof TypeError;}})).toBe(true);
    await page.evaluate(()=>(window as any).edge.destroy());await expect(page.locator('#edge')).toHaveCSS('view-transition-name','none');await expect(page.locator('#edge')).not.toHaveAttribute('data-lq-scroll-edge');
  });

  test('topbar twenty mount open close destroy cycles release listeners and preserve native values',async({page})=>{
    await page.setViewportSize({width:390,height:844});await mountTopbar(page,{enhance:false});
    const result=await page.evaluate(async()=>{
      const w=window as any,records:any[]=[],add=EventTarget.prototype.addEventListener,remove=EventTarget.prototype.removeEventListener,RO=window.ResizeObserver,observers=new Set();
      EventTarget.prototype.addEventListener=function(type,fn,opts){const capture=typeof opts==='boolean'?opts:Boolean(opts?.capture);if(!records.some(r=>r.target===this&&r.type===type&&r.fn===fn&&r.capture===capture))records.push({target:this,type,fn,capture});return add.call(this,type,fn,opts);};
      EventTarget.prototype.removeEventListener=function(type,fn,opts){const capture=typeof opts==='boolean'?opts:Boolean(opts?.capture),index=records.findIndex(r=>r.target===this&&r.type===type&&r.fn===fn&&r.capture===capture);if(index!==-1)records.splice(index,1);return remove.call(this,type,fn,opts);};
      window.ResizeObserver=class extends RO{observe(...args:any[]){observers.add(this);return super.observe(args[0],args[1]);}disconnect(){observers.delete(this);return super.disconnect();}};
      try{for(let i=0;i<20;i++){const h=w.api.enhanceShell(w.topbarRoot);if(w.api.enhanceShell(w.topbarRoot)!==h)throw Error('duplicate owner');h.openPane('actions');await h.closePane();h.destroy();h.destroy();await new Promise(requestAnimationFrame);}return {listeners:records.map(r=>r.type),observers:observers.size,same:w.topbarButton.parentNode===w.topbarParent,frameSame:w.topbarFrame.contentWindow.frameIdentity===w.frameIdentity,loads:w.frameLoads,modal:document.querySelectorAll('dialog:modal').length,bodyOverflow:document.body.style.overflow};}
      finally{EventTarget.prototype.addEventListener=add;EventTarget.prototype.removeEventListener=remove;window.ResizeObserver=RO;}
    });
    expect(result).toEqual({listeners:[],observers:0,same:true,frameSame:true,loads:1,modal:0,bodyOverflow:''});
  });

  test('topbar coarse more and primary controls stay at least 44 pixels under reduced and forced colors',async({browser})=>{
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,reducedMotion:'reduce'});
    try{const page=await context.newPage();await mountTopbar(page);await page.locator('#live-topbar > [data-lq-pane-open]').tap();
      for(const rect of await page.locator('#live-topbar > [data-lq-pane-open],#live-topbar [data-lq-pane-close].lq-shell-pane__close,#live-topbar .lq-btn').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>({w:el.getBoundingClientRect().width,h:el.getBoundingClientRect().height})))){expect(rect.w).toBeGreaterThanOrEqual(44);expect(rect.h).toBeGreaterThanOrEqual(44);}
      await page.emulateMedia({forcedColors:'active'});await page.locator('#live-topbar [data-lq-pane-close].lq-shell-pane__close').focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');await expect(page.locator('#live-topbar [data-lq-pane-close].lq-shell-pane__close')).toBeFocused();await expect(page.locator('#live-topbar [data-lq-pane-close].lq-shell-pane__close')).toHaveCSS('outline-style','solid');
      const scan=await new AxeBuilder({page}).analyze();expect(scan.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))).toEqual([]);
      await page.keyboard.press('Escape');await expect(page.locator('#topbar-submit')).toBeHidden();
    }finally{await context.close();}
  });

  test('Steps nodes and Crumbs registered chevrons have exact sizes and bounded motion',async({page})=>{
    await mount(page,{live:false});await gallery(page,false);
    await page.locator('[data-lq-step="current"] .lq-steps__node').evaluate(el=>Promise.all(el.getAnimations().map(a=>a.finished)));
    expect(await page.locator('.lq-steps__node').evaluateAll(els=>els.map(el=>[el.getBoundingClientRect().width,el.getBoundingClientRect().height]))).toEqual([[28,28],[28,28],[28,28]]);
    await expect(page.locator('[data-lq-step="current"] .lq-steps__node')).toHaveCSS('animation-iteration-count','1');
    await expect(page.locator('.lq-crumbs__separator .lq-icon').first()).toHaveCSS('width','14px');
    await page.emulateMedia({reducedMotion:'reduce'});await expect(page.locator('[data-lq-step="current"] .lq-steps__node')).toHaveCSS('animation-name','none');
    await page.emulateMedia({forcedColors:'active',reducedMotion:'no-preference'});await expect(page.locator('[data-lq-step="current"] .lq-steps__node')).toHaveCSS('animation-name','none');
  });

  test('S3 discovered current and complete token references resolve to their exact six-palette pairs', async ({page}) => {
    test.setTimeout(90_000);
    for (const palette of palettes) for (const appearance of ['light','dark']) {
      await mount(page,{live:false,palette,appearance}); await gallery(page);
      const result = await page.evaluate(() => {
        const probe = document.createElement('span'); document.body.append(probe);
        const expected = (fg:string,bg:string) => {
          probe.style.color=`hsl(var(${fg}))`;probe.style.backgroundColor=`hsl(var(${bg}))`;
          const style=getComputedStyle(probe);return [style.color,style.backgroundColor];
        };
        const currentPair=expected('--ls-on-primary-soft','--ls-primary-soft');
        const completePair=expected('--ls-tone-success-fg','--ls-tone-success-soft');
        const pairs=(selector:string)=>Array.from(document.querySelectorAll(selector),node=>{const style=getComputedStyle(node);return [style.color,style.backgroundColor];});
        const current=pairs('.lq-nav-item[aria-current],.lq-dock__item[aria-current],.lq-steps [aria-current="step"]');
        const complete=pairs('.lq-steps [data-lq-step="complete"] .lq-nav-item,.lq-steps [data-lq-step="complete"] .lq-steps__node');
        const borders=Array.from(document.querySelectorAll('.lq-steps [data-lq-step="complete"] .lq-steps__node'),node=>getComputedStyle(node).borderTopColor);
        probe.remove();return {currentPair,completePair,current,complete,borders};
      });
      expect(result.current.length).toBeGreaterThanOrEqual(3); expect(result.complete.length).toBe(2);
      for(const pair of result.current)expect(pair).toEqual(result.currentPair);
      for(const pair of result.complete)expect(pair).toEqual(result.completePair);
      expect(result.borders).toEqual([result.completePair[0]]);
    }
  });

  for(const palette of palettes)for(const appearance of ['light','dark'])test(`axe real editor and open pane ${palette}/${appearance}`,async({page})=>{
    expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokens.source_sha256);
    await mount(page,{palette,appearance});
    expect(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--ls-primary').trim())).toBe(tokens.themes[palette][appearance]['--ls-primary']);
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:980});if(width===390)await page.evaluate(()=>(window as any).handle.openPane('rail'));
      const scan=await new AxeBuilder({page}).analyze();expect(scan.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))).toEqual([]);
      if(palette==='rose'&&appearance==='dark'&&width===390)await page.screenshot({path:'.codex-temp/lq-shells-editor-dark-mobile.png',fullPage:true});
    }
    await mount(page,{live:false,palette,appearance});
    await gallery(page);
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:980});const scan=await new AxeBuilder({page}).analyze();
      expect(scan.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))).toEqual([]);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      if(width===390){
        await page.locator('#topbar > [data-lq-pane-open]').click();
        const openScan=await new AxeBuilder({page}).analyze();expect(openScan.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))).toEqual([]);
        if(palette==='rose'&&appearance==='dark')await page.screenshot({path:'.codex-temp/lq-shells-topbar-more-dark-mobile.png',fullPage:true});
        await page.keyboard.press('Escape');await expect(page.locator('#topbar--lq-actions')).toBeHidden();
      }
      if(width===1440&&palette==='rose'&&appearance==='dark')await page.screenshot({path:'.codex-temp/lq-shells-navigation-dark-desktop.png',fullPage:true});
      const pair=await page.locator('#fab-prominent').evaluate(el=>{const s=getComputedStyle(el),sample=document.createElement('span');sample.style.cssText='background:hsl(var(--ls-primary));color:hsl(var(--ls-on-primary))';document.body.append(sample);const expected=getComputedStyle(sample),result={actual:[s.backgroundColor,s.color],expected:[expected.backgroundColor,expected.color],blur:s.backdropFilter};sample.remove();return result;});expect(pair.actual).toEqual(pair.expected);expect(pair.blur).toBe('none');
      const strong=await page.locator('#sidebar .lq-sidebar__surface').evaluate(el=>getComputedStyle(el).getPropertyValue('--lq-material-fill').trim());
      expect(strong).toBe(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--ls-glass-fill-strong').trim()));
    }
  });
});
