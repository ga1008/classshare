import {test,expect,type Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page:Page) {
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!=='http://lq-schedule.test')throw new Error(`Unexpected network ${url.origin}`);
    if(url.pathname.startsWith('/static/js/')||url.pathname==='/static/css/tailwind-app.css'){
      const file=path.resolve(`.${url.pathname}`);
      if(!file.startsWith(path.resolve('static')+path.sep)||!fs.existsSync(file))throw new Error(`Unexpected file ${url.pathname}`);
      await route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file)});return;
    }
    if(url.pathname!=='/')throw new Error(`Unexpected fixture URL ${url.pathname}`);
    await route.fulfill({contentType:'text/html',body:`<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><body style="overflow:clip;padding-right:7px">
      <button id="parent-trigger">Open native parent</button><dialog id="parent"><button id="parent-hit">parent hit</button><div id="slot"></div></dialog>
      <div id="storage"><div id="deck"></div><div id="deck2"></div></div>
      <script type="module">
        import {createScheduleDeck} from '/static/js/course_schedule_deck.js';import {connectScheduleLayer} from '/static/js/lq/schedule-bridge.js';
        import {getLayerSystem} from '/static/js/lq/layer.js';import {openExplanation} from '/static/js/ui_explanation.js';
        window.layers=getLayerSystem(document);window.stats={hits:0,navigation:[],focus:[]};
        const overview={selected_term:{label:'第一学期'},section_range:{min:1,max:11},filters:{course_options:['课程']},weeks:[{week_index:1,label:'第1周',is_current:true,lessons:[{event_key:'lesson',course_name:'课程',weekday:1,sections:[2,3],hours:2,classroom:'B416',class_label:'2601班',classroom_url:'/classroom/1'}]}]};
        overview.weeks.push({week_index:2,label:'第2周',lessons:[]});
        window.deck=createScheduleDeck(document.getElementById('deck'),{onNavigate:url=>stats.navigation.push(url)});deck.setOverview(overview);
        window.deck2=createScheduleDeck(document.getElementById('deck2'));deck2.setOverview(overview);
        window.connection=connectScheduleLayer(deck);window.connection2=connectScheduleLayer(deck2);
        window.sameConnection=connectScheduleLayer(deck)===connection;
        window.expanded=deck.overlay.getRoot();window.expanded2=deck2.overlay.getRoot();
        expanded.id='expanded';expanded2.id='expanded2';
        const childButton=document.createElement('button');childButton.id='child-open';childButton.textContent='native child';expanded.querySelector('.cs-expand__nav').append(childButton);
        childButton.onclick=()=>{const root=document.createElement('dialog');root.id='child';root.innerHTML='<button id="child-hit">Child hit</button>';window.child=layers.open(root,{trigger:childButton});root.querySelector('button').onclick=()=>stats.hits++;};
        const helpButton=document.createElement('button');helpButton.id='help';helpButton.textContent='help';expanded.querySelector('.cs-expand__nav').append(helpButton);helpButton.onclick=()=>openExplanation(helpButton,{text:'课表说明'});
        document.getElementById('parent-trigger').onclick=()=>{document.getElementById('slot').append(document.getElementById('deck'));window.parentLayer=layers.open(document.getElementById('parent'),{trigger:document.getElementById('parent-trigger')});};
        window.ready=true;
      </script></body></html>`});
  });
  await page.goto('http://lq-schedule.test/');await page.waitForFunction(()=>(window as any).ready);return errors;
}
const expanded=(page:Page)=>page.locator('#expanded');
async function openDeck(page:Page){await page.locator('#deck .cs-card.is-active .cs-card__bar').click();await expect(expanded(page)).toBeVisible();await expect(expanded(page)).toHaveClass(/is-open/);}
async function animations(page:Page){await expanded(page).evaluate(async node=>{await Promise.allSettled(node.getAnimations({subtree:true}).map(animation=>animation.finished));});}
async function clickHit(page:Page,selector:string){
  await expect.poll(()=>page.locator(selector).evaluate(node=>{const box=node.getBoundingClientRect(),hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);return node===hit||node.contains(hit);})).toBe(true);
  await page.locator(selector).click();
}

test('portable owner closes its lesson preview before the week without installing another scroll lock',async({page})=>{
  const errors=await mount(page);await openDeck(page);await animations(page);
  await page.locator('#expanded [data-event-key="lesson"]').focus();await expect(page.locator('#expanded .is-preview.cs-lesson')).toHaveCount(1);
  await page.keyboard.press('Escape');await expect(page.locator('#expanded .is-preview.cs-lesson')).toHaveCount(0);await expect(expanded(page)).toBeVisible();
  await page.keyboard.press('Escape');await expect(expanded(page)).toBeHidden();await expect(page.locator('#deck .cs-stage')).toBeFocused();
  await expect(page.locator('body')).toHaveCSS('overflow','clip');await expect(page.locator('body')).toHaveCSS('padding-right','7px');
  expect(await page.evaluate(()=>(window as any).sameConnection)).toBe(true);expect(errors).toEqual([]);
});

test('native parent, external week and native child remain clickable and consume Escape one at a time',async({page})=>{
  const errors=await mount(page);await page.locator('#parent-trigger').click();await openDeck(page);
  expect(await expanded(page).evaluate(node=>node.closest('dialog')?.id)).toBe('parent');
  await expect(page.locator('body')).toHaveCSS('overflow','hidden');
  await clickHit(page,'#child-open');await clickHit(page,'#child-hit');expect(await page.evaluate(()=>(window as any).stats.hits)).toBe(1);
  await page.keyboard.press('Escape');await expect(page.locator('#child')).toHaveCount(0);await expect(expanded(page)).toBeVisible();await expect(page.locator('#child-open')).toBeFocused();
  await page.keyboard.press('Escape');await expect(expanded(page)).toBeHidden();await expect(page.locator('#parent')).toBeVisible();
  await clickHit(page,'#parent-hit');await expect(page.locator('body')).toHaveCSS('overflow','hidden');
  await page.keyboard.press('Escape');await expect(page.locator('#parent')).toBeHidden();await expect(page.locator('#parent-trigger')).toBeFocused();await expect(page.locator('body')).toHaveCSS('overflow','clip');expect(errors).toEqual([]);
});

test('explanation stays first, then the ordered week, then its underlying native owner',async({page})=>{
  const errors=await mount(page);await page.locator('#parent-trigger').click();await openDeck(page);await clickHit(page,'#help');
  await expect(page.locator('#ui-explanation-popover')).toBeVisible();await page.keyboard.press('Escape');await expect(page.locator('#ui-explanation-popover')).toBeHidden();
  await expect(expanded(page)).toBeVisible();await page.keyboard.press('Escape');await expect(expanded(page)).toBeHidden();await expect(page.locator('#parent')).toBeVisible();expect(errors).toEqual([]);
});

test('two external weeks use actual open order and the portable trap yields to later layers',async({page})=>{
  await mount(page);await openDeck(page);
  await page.evaluate(()=>(window as any).deck2.openExpanded());await expect(page.locator('#expanded2')).toBeVisible();
  await clickHit(page,'#expanded2 [data-csd-expand-next]');await page.keyboard.press('Escape');await expect(page.locator('#expanded2')).toBeHidden();await expect(expanded(page)).toBeVisible();
  await page.locator('#help').focus();await page.keyboard.press('Tab');await expect(page.locator('#expanded [data-event-key="lesson"]')).toBeFocused();
  await page.keyboard.press('Escape');await expect(expanded(page)).toBeVisible();await page.keyboard.press('Escape');await expect(expanded(page)).toBeHidden();
});

test('parent destruction force closes the complete week and native child without schedule return focus',async({page})=>{
  const errors=await mount(page);await page.locator('#parent-trigger').click();await openDeck(page);await clickHit(page,'#child-open');
  await page.evaluate(()=>{const w=window as any;const target=w.deck.overlay.getTrigger();target.focus=()=>w.stats.focus.push('schedule-return');w.parentLayer.destroy();});
  await expect(expanded(page)).toBeHidden();await expect(page.locator('#child')).toHaveCount(0);await expect(page.locator('#parent')).toBeHidden();
  expect(await page.evaluate(()=>(window as any).stats.focus)).toEqual([]);expect(await expanded(page).evaluate(node=>node.parentElement?.tagName)).toBe('BODY');await expect(page.locator('body')).toHaveCSS('overflow','clip');expect(errors).toEqual([]);
});

test('an unregistered native host closes the external week and restores its original DOM location',async({page})=>{
  const errors=await mount(page);await page.evaluate(()=>{document.getElementById('slot')!.append(document.getElementById('deck')!);(document.getElementById('parent') as HTMLDialogElement).showModal();});await openDeck(page);
  await page.evaluate(()=>(document.getElementById('parent') as HTMLDialogElement).close());await expect(expanded(page)).toBeHidden();
  expect(await expanded(page).evaluate(node=>node.parentElement?.tagName)).toBe('BODY');await expect(page.locator('body')).toHaveCSS('overflow','clip');expect(errors).toEqual([]);
});

test('closing presence and reopening preserve generation while owner removal cancels the final exit',async({page})=>{
  const errors=await mount(page);await openDeck(page);await animations(page);
  await page.locator('#expanded [data-csd-expand-close]').click();await page.evaluate(()=>(window as any).deck.openExpanded());await animations(page);
  await expect(expanded(page)).toBeVisible();await expect(expanded(page)).toHaveAttribute('data-lq-layer-state','open');
  await page.evaluate(()=>document.getElementById('deck')!.remove());await expect(expanded(page)).toBeHidden();expect(errors).toEqual([]);
});

test('destroy is idempotent and restores borrowed metadata without leaving connected overlay owners',async({page})=>{
  const errors=await mount(page);
  await page.evaluate(()=>{const root=(window as any).expanded as HTMLElement;root.dataset.lqLayerState='legacy';root.style.setProperty('--lq-layer-order','91','important');});
  await openDeck(page);await page.evaluate(()=>{const w=window as any;w.connection.destroy();w.connection.destroy();});
  await expect(expanded(page)).toBeHidden();await expect(expanded(page)).toHaveAttribute('data-lq-layer-state','legacy');
  expect(await expanded(page).evaluate((node:HTMLElement)=>[node.style.getPropertyValue('--lq-layer-order'),node.style.getPropertyPriority('--lq-layer-order')])).toEqual(['91','important']);
  await page.evaluate(()=>{const w=window as any;w.deck.destroy();w.deck.destroy();});await expect(expanded(page)).toHaveCount(0);expect(errors).toEqual([]);
});

test('a child dirty veto remains part of the native parent close chain through an external week',async({page})=>{
  await mount(page);await page.locator('#parent-trigger').click();await openDeck(page);await clickHit(page,'#child-open');
  const closed=await page.evaluate(async()=>{const w=window as any;w.child.update({beforeClose:()=>false});return w.layers.close(w.parentLayer);});
  expect(closed).toBe(false);await expect(page.locator('#child')).toBeVisible();await expect(expanded(page)).toBeVisible();await expect(page.locator('#parent')).toBeVisible();
  await page.evaluate(async()=>{const w=window as any;w.child.update({beforeClose:()=>true});await w.layers.close(w.parentLayer);});
  await expect(page.locator('#child')).toHaveCount(0);await expect(expanded(page)).toBeHidden();await expect(page.locator('#parent')).toBeHidden();await expect(page.locator('#parent-trigger')).toBeFocused();
});

test('390px native child is physically reachable while the portable week retains its own layout',async({page})=>{
  await page.setViewportSize({width:390,height:844});const errors=await mount(page);await page.locator('#parent-trigger').click();await openDeck(page);
  await clickHit(page,'#child-open');await clickHit(page,'#child-hit');await page.keyboard.press('Escape');await expect(page.locator('#child')).toHaveCount(0);
  await expect(expanded(page)).toBeVisible();await page.keyboard.press('Escape');await expect(expanded(page)).toBeHidden();await expect(page.locator('#parent')).toBeVisible();expect(errors).toEqual([]);
});
