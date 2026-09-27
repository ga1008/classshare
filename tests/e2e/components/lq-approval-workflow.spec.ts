import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page) {
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('https://approval.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file=path.resolve('.'+url.pathname);
      if (!file.startsWith(path.resolve('static')+path.sep)||!fs.existsSync(file)) throw Error(`Unexpected asset ${file}`);
      return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file)});
    }
    return route.fulfill({contentType:'text/html; charset=utf-8',body:`<!doctype html><html lang="zh" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{padding:40px}#launcher{margin:80px}</style></head><body><button id="outside">页面操作</button><span id="launcher"></span><div id="panel"></div><script type="module">
      import * as approval from '/static/js/approval_workflow.js';
      import {getLayerSystem} from '/static/js/lq/layer.js';
      import {openDialog} from '/static/js/lq/dialogs.js';
      const item=(id=1)=>({id,request_type:'submission_withdraw',request_type_label:'撤回提交',title:'申请 '+id,status:'pending',status_label:'待审批',reason:'测试理由 '+id,applicant_name:'学生甲',created_at:'2026-09-27T12:00:00',can_decide:true,detail:{recommended_resubmission_due_at:'2026-09-30T18:00:00'},events:[]});
      const server={items:[item(1),item(2)],calls:[],hold:new Set(),releases:new Map(),fail:new Set()};
      window.fetch=async(url,opts={})=>{
        const parsed=new URL(url,location.href),key=parsed.pathname.replace('/api/approvals',''),method=opts.method||'GET';
        const call=method+' '+key;server.calls.push({call,body:opts.body?JSON.parse(opts.body):null});
        if(server.hold.has(call)) await new Promise(resolve=>server.releases.set(call,resolve));
        if(server.fail.has(call)) return new Response(JSON.stringify({detail:'测试失败'}),{status:500});
        let data;
        if(method==='GET'&&!key) data={items:server.items,pending_count:server.items.filter(x=>x.status==='pending').length};
        else if(method==='POST'&&!key) {const created={...item(3),reason:JSON.parse(opts.body).reason};server.items.push(created);data={request:created};}
        else {const [,id,action]=key.split('/'),found=server.items.find(x=>x.id===Number(id));
          if(method==='POST') {found.status=action==='cancel'?'cancelled':action==='reject'?'rejected':'approved';found.status_label=found.status;found.can_decide=false;}
          data={request:found};}
        return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
      };
      window.fixture={approval,layer:getLayerSystem(),openDialog,server};window.ready=true;
    </script></body></html>`});
  });
  await page.goto('https://approval.test/');await page.waitForFunction(()=>(window as any).ready);
  return errors;
}
async function panel(page:Page) {
  await page.evaluate(()=>{const w=window as any;w.decided=[];w.panel=w.fixture.approval.mountPanel(document.getElementById('panel'),{startOpen:true,onDecided:(item:any)=>w.decided.push(item.id)});});
  await expect(page.locator('[data-apr-open]')).toHaveCount(2);
  await page.locator('[data-apr-open="1"]').click();await expect(page.locator('[data-apr-field="note"]')).toBeVisible();
}

test('applicant keeps drafts across back, dismissal and failed submission; pending submit/cancel deny close once',async({page})=>{
  const errors=await mount(page);
  await page.evaluate(()=>{const w=window as any;w.launcher=w.fixture.approval.mountLauncher(document.getElementById('launcher'),{requestType:'submission_withdraw',subjectId:10});});
  await page.locator('.apr-trigger').click();await page.locator('[data-apr-start]').click();
  await page.locator('.apr-pop-textarea').fill('原始申请草稿');await page.locator('[data-apr-back]').click();await page.locator('[data-apr-start]').click();
  await expect(page.locator('.apr-pop-textarea')).toHaveValue('原始申请草稿');
  await page.keyboard.press('Escape');await expect(page.locator('.apr-popover')).toHaveCount(0);
  await page.locator('.apr-trigger').click();await expect(page.locator('.apr-pop-textarea')).toHaveValue('原始申请草稿');
  await page.evaluate(()=>{const s=(window as any).fixture.server;s.hold.add('POST ');s.fail.add('POST ');});
  await page.locator('[data-apr-submit]').click();await expect(page.locator('[data-apr-submit]')).toHaveText('提交中…');
  expect(await page.evaluate(()=>(window as any).launcher.close())).toBe(false);
  await page.evaluate(()=>(window as any).fixture.server.releases.get('POST ')());
  await expect(page.locator('[data-apr-submit]')).toBeEnabled();await expect(page.locator('.apr-pop-textarea')).toHaveValue('原始申请草稿');
  await page.evaluate(()=>{const s=(window as any).fixture.server;s.hold.delete('POST ');s.fail.delete('POST ');});
  await page.locator('[data-apr-submit]').click();await expect(page.locator('[data-apr-cancel]')).toBeVisible();
  await page.evaluate(()=>(window as any).fixture.server.hold.add('POST /3/cancel'));
  await page.locator('[data-apr-cancel]').click();expect(await page.evaluate(()=>(window as any).launcher.close())).toBe(false);
  await page.evaluate(()=>(window as any).fixture.server.releases.get('POST /3/cancel')());
  await expect(page.locator('[data-apr-start]')).toBeVisible();
  expect(await page.evaluate(()=>(window as any).fixture.server.calls.filter((c:any)=>c.call==='POST /3/cancel').length)).toBe(1);
  expect(errors).toEqual([]);
});

test('approval layers preserve per-request drafts, nested Escape, focus and original scroll ownership',async({page})=>{
  const errors=await mount(page);await page.evaluate(()=>document.body.style.overflow='clip');await panel(page);
  await page.locator('[data-apr-field="note"]').fill('审批草稿');await page.locator('[data-apr-field="extension_minutes"]').fill('120');
  await page.evaluate(()=>{const w=window as any;w.child=w.fixture.openDialog({title:'嵌套核对',body:'确认内容'},{trigger:document.querySelector('[data-apr-decide="approve"]')});});
  await expect(page.getByText('嵌套核对')).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByText('嵌套核对')).toHaveCount(0);
  await expect(page.locator('[data-apr-field="note"]')).toBeVisible();expect(await page.evaluate(()=>document.body.style.overflow)).toBe('hidden');
  await page.locator('[data-apr-modal-close]').click();await expect(page.locator('.apr-modal')).toHaveCount(0);
  expect(await page.evaluate(()=>document.body.style.overflow)).toBe('clip');await expect(page.locator('[data-apr-open="1"]')).toBeFocused();
  await page.locator('[data-apr-open="2"]').click();await expect(page.locator('[data-apr-field="note"]')).toHaveValue('');
  await page.locator('[data-apr-modal-close]').click();await expect(page.locator('.apr-modal')).toHaveCount(0);
  await page.locator('[data-apr-open="1"]').click();await expect(page.locator('[data-apr-field="note"]')).toHaveValue('审批草稿');
  await expect(page.locator('[data-apr-field="extension_minutes"]')).toHaveValue('120');
  expect(await page.evaluate(()=>[...document.querySelectorAll('.apr-modal button,.apr-modal input,.apr-modal textarea,.apr-drawer button')].every((n:any)=>Boolean(n.dataset.lqComponent)))).toBe(true);
  expect(errors).toEqual([]);
});

test('pending decisions veto own and parent close, prevent duplicate posts and retain exact decision payload',async({page})=>{
  const errors=await mount(page);await panel(page);
  await page.locator('[data-apr-decide="reject"]').click();expect(await page.evaluate(()=>(window as any).fixture.server.calls.some((c:any)=>c.call==='POST /1/reject'))).toBe(false);
  await page.locator('[data-apr-field="note"]').fill('需要补齐附件');await page.locator('[data-apr-field="extension_minutes"]').fill('90');
  await page.evaluate(()=>(window as any).fixture.server.hold.add('POST /1/reject'));
  await page.locator('[data-apr-decide="reject"]').click();
  expect(await page.evaluate(()=>(window as any).panel.close())).toBe(false);expect(await page.evaluate(()=>(window as any).panel.setDrawer(false))).toBe(false);
  await page.keyboard.press('Escape');await expect(page.locator('.apr-modal')).toBeVisible();
  await page.evaluate(()=>(window as any).fixture.server.releases.get('POST /1/reject')());
  await expect(page.locator('[data-apr-decision]')).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).fixture.server.calls.filter((c:any)=>c.call==='POST /1/reject'))).toEqual([{call:'POST /1/reject',body:{note:'需要补齐附件',decision_payload:{resubmission_due_at:'2026-09-30T18:00',extension_minutes:90}}}]);
  expect(await page.evaluate(()=>(window as any).decided)).toEqual([1]);
  await page.locator('[data-apr-modal-close]').click();await expect(page.locator('.apr-modal')).toHaveCount(0);expect(errors).toEqual([]);
});

test('late detail responses cannot overwrite the selected request or resurrect a closed/destroyed layer',async({page})=>{
  const errors=await mount(page);
  await page.evaluate(()=>{const w=window as any;w.fixture.server.hold.add('GET /1');w.panel=w.fixture.approval.mountPanel(document.getElementById('panel'),{startOpen:true});void w.panel.open(1);});
  await page.evaluate(()=>(window as any).panel.open(2));await expect(page.locator('[data-apr-modal-title]')).toHaveText('申请 2');
  await page.evaluate(()=>(window as any).fixture.server.releases.get('GET /1')());await expect(page.locator('[data-apr-modal-title]')).toHaveText('申请 2');
  await page.locator('[data-apr-modal-close]').click();await expect(page.locator('.apr-modal')).toHaveCount(0);
  await page.evaluate(()=>{const w=window as any;void w.panel.open(1);});
  await page.waitForFunction(()=>(window as any).fixture.server.calls.filter((c:any)=>c.call==='GET /1').length===2);
  await page.evaluate(()=>(window as any).panel.destroy());await page.evaluate(()=>(window as any).fixture.server.releases.get('GET /1')());
  await expect(page.locator('.apr-modal,.apr-drawer')).toHaveCount(0);expect(await page.evaluate(()=>(window as any).fixture.layer.top())).toBe(null);expect(errors).toEqual([]);
});

test('approval detail stays within narrow screens in both appearances',async({page})=>{
  const errors=await mount(page);await panel(page);
  for(const appearance of ['light','dark']) for(const width of [1440,390]) {
    await page.setViewportSize({width,height:844});await page.evaluate(mode=>document.documentElement.dataset.appearance=mode,appearance);
    const geometry=await page.locator('.apr-modal .lq-dialog__surface').evaluate(node=>{
      const rect=node.getBoundingClientRect(),body=node.querySelector('.apr-modal-body')!,style=getComputedStyle(node);
      return {x:rect.x,right:rect.right,bottom:rect.bottom,top:rect.top,background:style.backgroundColor,scroll:body.scrollHeight>=body.clientHeight};
    });
    expect(geometry.x).toBeGreaterThanOrEqual(-1);expect(geometry.right).toBeLessThanOrEqual(width+1);expect(geometry.top).toBeGreaterThanOrEqual(-1);expect(geometry.bottom).toBeLessThanOrEqual(845);
    expect(geometry.scroll).toBe(true);expect(geometry.background).not.toBe('rgba(0, 0, 0, 0)');
  }
  expect(errors).toEqual([]);
});

test('review disclosure keeps its native toggle and embedded review URL', async ({ page }) => {
  const errors = await mount(page);
  await page.evaluate(() => { (window as any).fixture.server.items[0].detail.review_url = 'about:blank#student-review'; });
  await panel(page);
  const review = page.locator('.apr-review');
  const summary = review.locator('summary');
  await expect(summary).toHaveAttribute('data-lq-component', 'disclosure');
  await expect(summary).toHaveClass('lq-disclosure-trigger');
  await expect(review.locator('iframe')).toHaveAttribute('src', 'about:blank#student-review');
  await expect(review).toHaveAttribute('open', '');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(review).not.toHaveAttribute('open');
  await page.keyboard.press('Enter');
  await expect(review).toHaveAttribute('open', '');
  await expect(review.locator('iframe')).toBeVisible();
  expect(errors).toEqual([]);
});
