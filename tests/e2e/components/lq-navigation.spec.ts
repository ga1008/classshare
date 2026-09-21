import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const fixture = JSON.parse(execFileSync(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python', ['tests/e2e/scripts/render_lq_navigation.py'], { encoding: 'utf8' }));
async function mount(page: Page, { entry = 'element', kind = 'tabs', options = {}, props = {} } = {}) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-navigation.test') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ navigation</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:16px;max-width:100%;box-sizing:border-box}h1{margin-bottom:16px}</style></head><body><main><h1>学习视图</h1><div id="fixture"></div><button id="after">继续</button></main><script type="module">import * as nav from '/static/js/lq/navigation.js';window.nav=nav;document.body.dataset.ready='true';</script></body></html>` });
    return route.fulfill({ status: 404, body: 'Missing fixture' });
  });
  await page.goto('https://lq-navigation.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ fixture, entry, kind, options, props }) => {
    const w = window as any, host = document.getElementById('fixture')!;
    if (entry === 'jinja') host.innerHTML = fixture.composition;
    else if (entry === 'html') host.innerHTML = w.nav.html[kind]({ ...fixture.cases[0].props, ...props });
    else host.append(w.nav.createNavigation(kind, { ...fixture.cases[0].props, ...props }));
    w.root = host.firstElementChild; w.changes = [];
    w.root.addEventListener('lq:tab-change', (e: CustomEvent) => w.changes.push({ key: e.detail.key, reason: e.detail.reason }));
    w.handle = w.nav.tabs(w.root, options);
  }, { fixture, entry, kind, options, props });
}

test('LQ navigation real Jinja, HTML and Element trees agree and reject invalid views', async ({ page }) => {
  expect(fixture.isolated).toBe(true);
  expect(fixture.cases.filter((c: any) => c.error)).toEqual([]);
  expect(fixture.invalid.every((c: any) => c.error === 'ValueError')).toBe(true);
  await mount(page);
  const actual = await page.evaluate(fixture => {
    const api = (window as any).nav;
    function semantic(node: Node): any {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent?.trim() ? { text: node.textContent } : null;
      const el = node as Element;
      return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).sort()), children: [...el.childNodes].map(semantic).filter(Boolean) };
    }
    const parse = (html: string) => { const root = document.createElement('template'); root.innerHTML = html; return [...root.content.childNodes].map(semantic).filter(Boolean); };
    return {
      cases: fixture.cases.map((c: any) => ({ tree: api.navigationProps(c.kind, c.props), jinja: parse(c.html), html: parse(api.navigationMarkup(c.kind, c.props)), element: [semantic(api.createNavigation(c.kind, c.props))] })),
      invalid: fixture.invalid.map((c: any) => ['navigationProps', 'navigationMarkup', 'createNavigation'].every(method => { try { api[method](c.kind, c.props); return false; } catch { return true; } })),
    };
  }, fixture);
  actual.cases.forEach((c: any, i: number) => { expect(c.tree).toEqual(fixture.cases[i].normalized); expect(c.html).toEqual(c.jinja); expect(c.element).toEqual(c.jinja); });
  expect(actual.invalid.every(Boolean)).toBe(true);
  await expect(page.locator('#fixture img')).toHaveCount(0);
});

for (const entry of ['element', 'html']) test(`LQ ${entry} tabs keyboard skips disabled, wraps and retains one active view`, async ({ page }) => {
  await mount(page, { entry });
  await page.getByRole('tab', { name: '课程概览 2' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '草稿' })).toBeFocused();
  await expect(page.getByRole('tab', { name: '草稿' })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '课程概览 2' })).toBeFocused();
  expect(await page.locator('[role=tab][tabindex="0"]').count()).toBe(1);
  expect(await page.locator('[role=tabpanel]:visible').count()).toBe(1);
  const before = await page.evaluate(() => (window as any).handle.value);
  await page.keyboard.press('ArrowDown');
  expect(await page.evaluate(() => (window as any).handle.value)).toBe(before);
});

test('LQ manual tabs preserve drafts and focus before hiding, with final-only change notification', async ({ page }) => {
  await mount(page, { entry: 'jinja', options: { activation: 'manual' } });
  await page.locator('#first-draft').fill('不可丢失的输入');
  await page.getByRole('tab', { name: '概览', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '草稿', exact: true })).toBeFocused();
  await expect(page.locator('#first-draft')).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page.locator('#second-draft')).toBeVisible();
  await page.locator('#second-draft').focus();
  const state = await page.evaluate(() => {
    const w = window as any, first = document.getElementById('first-draft');
    w.changes.length = 0;
    w.handle.select('first'); w.handle.select('second'); w.handle.select('first');
    return { sameNode: first === document.getElementById('first-draft'), focused: document.activeElement?.id };
  });
  expect(state).toEqual({ sameNode: true, focused: 'composed--lq-tab-first' });
  await expect(page.locator('#first-draft')).toHaveValue('不可丢失的输入');
  await expect.poll(() => page.evaluate(() => (window as any).changes)).toEqual([{ key: 'first', reason: 'programmatic' }]);
  await page.getByRole('tab', { name: '草稿', exact: true }).focus();
  await page.locator('#after').focus();
  await expect(page.getByRole('tab', { name: '概览', exact: true })).toHaveAttribute('tabindex', '0');
});

test('LQ tabs history/persist accept only enabled known panels and preserve history state', async ({ page }) => {
  const persist = { identity: 'teacher-1', resource: 'offering-4', key: 'views' };
  await mount(page, { options: { hash: 'push', persist } });
  await page.evaluate(() => { history.replaceState({ unrelated: 42 }, ''); (window as any).handle.select('second'); (window as any).handle.select('third'); });
  await expect(page).toHaveURL(/#navigation--lq-panel-third$/);
  await page.goBack();
  await expect(page.getByRole('tab', { name: '草稿' })).toHaveAttribute('aria-selected', 'true');
  expect(await page.evaluate(() => history.state)).toEqual({ unrelated: 42 });
  await page.evaluate(() => { location.hash = 'navigation--lq-panel-disabled'; });
  await expect(page).toHaveURL(/disabled$/);
  await expect(page.getByRole('tab', { name: '草稿' })).toHaveAttribute('aria-selected', 'true');
  expect(await page.evaluate(() => {
    const w = window as any;
    w.handle.destroy(); history.replaceState(null, '', '#invalid');
    w.handle = w.nav.tabs(w.root, { persist: { identity: 'teacher-2', resource: 'offering-4', key: 'views' } });
    const separate = w.handle.value; w.handle.destroy();
    w.handle = w.nav.tabs(w.root, { persist: { identity: 'teacher-1', resource: 'offering-4', key: 'views' } });
    return { separate, remembered: w.handle.value };
  })).toEqual({ separate: 'second', remembered: 'third' });
});

test('LQ tabs survive blocked storage, respect IME and dispose duplicate ownership/resources', async ({ page }) => {
  await page.addInitScript(() => { Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } }); });
  await mount(page, { options: { persist: { identity: '1', resource: '4', key: 'tabs' } } });
  const result = await page.evaluate(async () => {
    const w = window as any;
    const duplicate = await import('/static/js/lq/navigation.js?second-url');
    const same = duplicate.tabs(w.root) === w.handle;
    const tab = w.root.querySelector('[role=tab]');
    tab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', isComposing: true, bubbles: true, cancelable: true }));
    const composing = w.handle.value;
    w.handle.select('second'); w.handle.destroy(); w.handle.destroy();
    w.changes.length = 0;
    const switched = w.handle.select('first'); tab.click();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const styles = w.root.querySelector('[role=tablist]').style.length;
    const changes = w.changes.length;
    const next = duplicate.tabs(w.root);
    return { same, composing, switched, styles, changes, recreated: next !== w.handle, value: next.value };
  });
  expect(result).toEqual({ same: true, composing: 'first', switched: false, styles: 0, changes: 0, recreated: true, value: 'second' });
});

test('LQ segment geometry, mobile targets, reduced/forced modes and semantic axe', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mount(page, { kind: 'segment' });
  await page.getByRole('tab', { name: '草稿' }).click();
  await expect.poll(() => page.locator('[role=tablist]').evaluate(el => parseFloat((el as HTMLElement).style.getPropertyValue('--lq-thumb-w')))).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await page.locator('[role=tablist]').evaluate(el => getComputedStyle(el, '::before').transitionProperty)).toBe('none');
  await page.getByRole('tab', { name: '课程概览 2' }).click();
  expect(await page.locator('[role=tabpanel]:visible').evaluate(el => el.getAnimations().length)).toBe(0);
  for (const mode of ['light', 'dark']) {
    await page.evaluate(mode => document.documentElement.dataset.appearance = mode, mode);
    const result = await new AxeBuilder({ page }).analyze();
    expect(result.violations, JSON.stringify(result.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })))).toEqual([]);
  }
  await page.emulateMedia({ forcedColors: 'active' });
  const highlight = await page.evaluate(() => { const el = document.createElement('span'); el.style.background = 'Highlight'; document.body.append(el); const color = getComputedStyle(el).backgroundColor; el.remove(); return color; });
  await expect(page.getByRole('tab', { name: '课程概览 2' })).toHaveCSS('background-color', highlight);
});

test('LQ tabs vertical/RTL navigation and nested nodes stay locally owned', async ({ page }) => {
  await mount(page, { props: { orientation: 'vertical' } });
  await page.getByRole('tab', { name: '课程概览 2' }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('tab', { name: '草稿' })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '草稿' })).toBeFocused();
  await page.evaluate(() => {
    const w = window as any;
    const child = w.nav.createSegment({ id: 'nested', label: '子视图', items: [{ key: 'a', label: '子一' }, { key: 'b', label: '子二' }] });
    child.dir = 'rtl'; w.root.querySelector('[role=tabpanel]:not([hidden])').append(child);
    w.child = w.nav.tabs(child);
  });
  await page.getByRole('tab', { name: '子一', exact: true }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: '子二', exact: true })).toBeFocused();
  expect(await page.evaluate(() => (window as any).handle.value)).toBe('second');
  expect(await page.evaluate(() => (window as any).child.value)).toBe('b');
});

test('LQ coarse segment targets remain separate at 390px and survive resize', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  try {
    await mount(page, { kind: 'segment' });
    const rects = await page.locator('[role=tab]').evaluateAll(elements => elements.map(el => { const r = el.getBoundingClientRect(); return { width: r.width, height: r.height, left: r.left, right: r.right }; }));
    rects.forEach(r => { expect(r.width).toBeGreaterThanOrEqual(44); expect(r.height).toBeGreaterThanOrEqual(44); });
    rects.slice(1).forEach((r, i) => expect(r.left).toBeGreaterThanOrEqual(rects[i].right));
    await page.getByRole('tab', { name: '草稿' }).tap();
    await page.setViewportSize({ width: 820, height: 844 });
    await expect.poll(() => page.locator('[role=tablist]').evaluate(el => {
      const tab = el.querySelector('[aria-selected=true]')!;
      return Math.abs(parseFloat((el as HTMLElement).style.getPropertyValue('--lq-thumb-w')) - tab.getBoundingClientRect().width);
    })).toBeLessThan(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await context.close(); }
});

test('LQ manual reselect repairs the roving tabstop and disabled refresh has a safe empty state',async({page})=>{
  await mount(page,{entry:'jinja',options:{activation:'manual'}});
  const first=page.getByRole('tab',{name:'概览',exact:true}),second=page.getByRole('tab',{name:'草稿',exact:true});
  await first.focus();await page.keyboard.press('ArrowRight');await expect(second).toHaveAttribute('tabindex','0');
  await first.click();await expect(first).toHaveAttribute('tabindex','0');await expect(second).toHaveAttribute('tabindex','-1');
  await page.locator('#first-draft').fill('组合输入法草稿');
  await page.evaluate(()=>{const w=window as any;w.root.querySelector('[data-lq-tab=first]').setAttribute('aria-disabled','true');w.handle.refresh();});
  await expect(second).toBeFocused();await expect(page.locator('#second-draft')).toBeVisible();
  await page.locator('#second-draft').focus();
  await page.evaluate(()=>{const w=window as any;w.root.querySelector('[data-lq-tab=second]').disabled=true;w.handle.refresh();});
  await expect(page.getByRole('tablist')).toBeFocused();expect(await page.locator('[role=tabpanel]:visible').count()).toBe(0);
  expect(await page.locator('[role=tab][aria-selected=true]').count()).toBe(0);
  await page.evaluate(()=>{const w=window as any;w.root.querySelector('[data-lq-tab=first]').removeAttribute('aria-disabled');w.handle.refresh();});
  await expect(first).toBeFocused();await expect(page.locator('#first-draft')).toHaveValue('组合输入法草稿');
  await expect(page.getByRole('tablist')).not.toHaveAttribute('tabindex');
});

test('LQ Node slots reject a later invalid or duplicate node before moving any live draft',async({page})=>{
  await mount(page);
  const result=await page.evaluate(()=>{
    const w=window as any,source=document.createElement('div'),draft=document.createElement('input');draft.value='原输入';source.append(draft);document.body.append(source);
    const props={id:'slots',label:'slots',items:[{key:'one',label:'One'},{key:'two',label:'Two'}]};
    let errors=0;
    for(const panels of [new Map([['one',draft],['two','unsafe']]),new Map([['one',draft],['two',draft]])]){
      try{w.nav.createTabs(props,document,panels);}catch{errors++;}
      if(draft.parentElement!==source)throw new Error('Caller node moved before validation');
    }
    let clicks=0;draft.addEventListener('click',()=>clicks++);
    const fragment=document.createDocumentFragment();fragment.append(document.createTextNode('Second panel'));
    const root=w.nav.createTabs(props,document,new Map([['one',draft],['two',fragment]]));document.body.append(root);const api=w.nav.tabs(root);
    api.select('two');api.select('one');draft.click();
    return {errors,same:root.querySelector('input')===draft,value:draft.value,clicks,text:root.textContent.includes('Second panel')};
  });
  expect(result).toEqual({errors:2,same:true,value:'原输入',clicks:1,text:true});
});

test('LQ rejects malformed authored relationships before binding a behavior owner',async({page})=>{
  await mount(page);
  const results=await page.evaluate(()=>{
    const w=window as any,props={id:'bad',label:'bad',items:[{key:'a',label:'A'},{key:'b',label:'B'}]};
    return [root=>root.querySelector('[role=tab]').removeAttribute('role'),root=>root.append(root.querySelector('[role=tablist]').cloneNode(true)),root=>root.querySelector('[data-lq-tab]').dataset.lqTab='',root=>root.querySelector('[role=tabpanel]').setAttribute('aria-labelledby','wrong')].map(mutate=>{
      const root=w.nav.createTabs(props);mutate(root);try{w.nav.tabs(root);return false;}catch{return !root[Symbol.for('lanshare.lq.tabs.v1')];}
    });
  });expect(results).toEqual([true,true,true,true]);
});

test('LQ persistence separates identity, resource and key while replacement hash keeps history entries',async({page})=>{
  await mount(page,{options:{hash:true,persist:{identity:'student-1',resource:'course-1',key:'navigation'}}});
  const result=await page.evaluate(()=>{
    const w=window as any,before=history.length;w.handle.select('third');const replaced=history.length===before;
    w.handle.destroy();history.replaceState(null,'','#foreign--lq-panel-third');
    const props={id:'fresh',label:'Fresh',items:[{key:'first',label:'First'},{key:'third',label:'Third'}]};
    const values=[['student-2','course-1','navigation'],['student-1','course-2','navigation'],['student-1','course-1','other'],['student-1','course-1','navigation']].map(([identity,resource,key])=>{
      const root=w.nav.createTabs(props);document.body.append(root);const api=w.nav.tabs(root,{hash:true,persist:{identity,resource,key}});const value=api.value;api.destroy();root.remove();return value;
    });return {replaced,values};
  });expect(result).toEqual({replaced:true,values:['first','first','first','third']});
});

test('LQ 120ms presence settles only the final callback and teardown restores borrowed styles',async({page})=>{
  await mount(page);
  const result=await page.evaluate(async()=>{
    const w=window as any;w.handle.destroy();const list=w.root.querySelector('[role=tablist]');list.style.setProperty('--lq-thumb-x','7px','important');list.setAttribute('data-lq-thumb','caller');
    const callbacks=[];let done;const settled=new Promise(resolve=>{done=resolve;});w.handle=w.nav.tabs(w.root,{onChange:detail=>{callbacks.push(detail.key);done();}});
    w.handle.select('second');const animation=w.root.querySelector('[role=tabpanel]:not([hidden])').getAnimations()[0];const duration=animation.effect.getTiming().duration;
    w.handle.select('third');await settled;
    w.handle.destroy();const restore=[list.style.getPropertyValue('--lq-thumb-x'),list.style.getPropertyPriority('--lq-thumb-x'),list.getAttribute('data-lq-thumb')];
    return {duration,callbacks,restore};
  });expect(result).toEqual({duration:120,callbacks:['third'],restore:['7px','important','caller']});
});

test('LQ reduced motion changes finish a running view and event teardown cancels a stale onChange',async({page})=>{
  await mount(page);
  await page.evaluate(()=>{const w=window as any;w.root.style.setProperty('--ls-dur-fast','5s');w.handle.select('second');});
  await page.emulateMedia({reducedMotion:'reduce'});
  await expect.poll(()=>page.evaluate(()=>(window as any).changes)).toEqual([{key:'second',reason:'programmatic'}]);
  expect(await page.locator('[role=tabpanel]:visible').evaluate(node=>node.getAnimations().length)).toBe(0);
  expect(await page.evaluate(async()=>{
    const w=window as any;w.handle.destroy();let callbacks=0;w.handle=w.nav.tabs(w.root,{onChange:()=>callbacks++});
    w.root.addEventListener('lq:tab-change',()=>w.handle.destroy(),{once:true});w.handle.select('third');await Promise.resolve();await Promise.resolve();return callbacks;
  })).toBe(0);
});

for(const direction of ['ltr','rtl'])test(`LQ segment thumb follows actual scroll and wrapped rows (${direction})`,async({page})=>{
  await page.setViewportSize({width:390,height:844});await mount(page,{kind:'segment'});
  await page.evaluate(dir=>{const w=window as any;w.root.dir=dir;w.handle.select('third',{focus:true});},direction);
  async function compare(){
    await page.locator('[role=tablist]').evaluate(async list=>{await Promise.allSettled(list.getAnimations({subtree:true}).map(animation=>animation.finished));});
    await expect.poll(()=>page.locator('[role=tablist]').evaluate(list=>{
      const css=getComputedStyle(list,'::before'),probe=document.createElement('span');
      for(const key of ['position','left','top','width','height','transform'])probe.style.setProperty(key,css.getPropertyValue(key));
      probe.style.pointerEvents='none';list.append(probe);const actual=probe.getBoundingClientRect(),tab=list.querySelector('[aria-selected=true]')!.getBoundingClientRect();probe.remove();
      return Math.max(Math.abs(actual.left-tab.left),Math.abs(actual.top-tab.top),Math.abs(actual.width-tab.width),Math.abs(actual.height-tab.height));
    })).toBeLessThan(1);
  }
  await compare();await page.locator('[role=tablist]').evaluate((node:HTMLElement)=>{node.scrollLeft/=2;});await compare();
  await page.locator('[role=tablist]').evaluate((node:HTMLElement)=>{node.style.flexWrap='wrap';node.querySelectorAll('.lq-tabs__label').forEach((label,index)=>label.textContent=`第${index+1}个视图说明`);});
  await compare();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('LQ six palettes in both appearances pass real CSS axe with readable selected views',async({page})=>{
  await page.setViewportSize({width:390,height:844});await mount(page,{kind:'segment',props:{items:[{key:'overview',label:'课程概览',panel:'保留课程输入与滚动位置。'},{key:'draft',label:'作业草稿',panel:'当前草稿尚未保存。',badge:2},{key:'history',label:'历史记录',panel:'查看已保存的记录。'}]}});
  await page.evaluate(()=>{const w=window as any;const root=w.nav.createTabs({id:'detail',label:'课程详情',items:[{key:'course',label:'当前课程',panel:'当前课程的信息清晰可见。'},{key:'records',label:'学习记录',panel:'保留原有业务内容。'}]});document.getElementById('fixture')!.append(root);w.lineHandle=w.nav.tabs(root);});
  fs.mkdirSync('.codex-temp/lq-s2-navigation-visual',{recursive:true});
  for(const mode of ['light','dark'])for(const palette of ['teal','indigo','sky','mint','violet','rose']){
    await page.evaluate(({mode,palette})=>{document.documentElement.dataset.appearance=mode;document.documentElement.dataset.uiPalette=palette;},{mode,palette});
    await page.getByRole('tab',{name:'作业草稿 2'}).click();
    const result=await new AxeBuilder({page}).analyze();expect(result.violations,`${mode}/${palette}: ${JSON.stringify(result.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})))}`).toEqual([]);
    await page.screenshot({path:`.codex-temp/lq-s2-navigation-visual/${mode}-${palette}.png`});
  }
});
