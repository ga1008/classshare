import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

const styles = ['tailwind-app.css', 'lesson_plan.css', 'grade_publication.css'].filter(name => fs.existsSync('static/css/'+name))
  .map(name => `<style>${fs.readFileSync('static/css/'+name,'utf8')}</style>`).join('');
async function mount(page: Page, merge=false) {
  const posts: any[] = [], errors: string[] = [];
  const state = {revision:'a'.repeat(64), status:409, hold:false, held:false, blocked:false, previews:0};
  let release!:()=>void;
  const gate = new Promise<void>(resolve=>{release=resolve;});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://teaching-lifecycle.test/**', async route=>{
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/static/')) return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync('.'+path,'utf8')});
    if (path.endsWith('/delete-impact')) return route.fulfill({json:{status:'success',review:{kind:'teaching_delete',title:'核对并删除班级',summary:'班级：待删空班（#7）',review_hash:state.revision,
      impact_sections:[],warnings:[{code:'irreversible_root_delete',message:'将永久删除班级，平台没有一键恢复入口。'}],blockers:state.blocked?['仍有关联学生，请先处理。']:[],can_execute:!state.blocked,expected_confirmation_text:'待删空班'}}});
    if (path.endsWith('/candidates')) return route.fulfill({json:{status:'success',candidates:[{course_name:'网络课程',semester:'2026秋',recommended_target_id:30,offerings:[
      {offering_id:30,class_name:'一班',student_count:10,assignment_count:1,session_count:1},
      {offering_id:31,class_name:'二班',student_count:10,assignment_count:1,session_count:1}]}]}});
    if (path.endsWith('/preview')) {
      state.previews++;
      const body=route.request().postDataJSON();
      const result={status:'success',preview:{can_execute:true,review_hash:state.revision,total_source_rows:1,target:{class_name:body.target_offering_id===30?'一班':'二班'},tables:[{label:'课堂作业',strategy:'assignment_coexist',source_rows:1}],warnings:[],blockers:[]}};
      if(state.hold){state.held=true;await gate;}
      return route.fulfill({json:result});
    }
    if(route.request().method()==='DELETE'||path.endsWith('/execute')) {
      posts.push(route.request().postDataJSON());
      if(state.hold){state.held=true;await gate;}
      return route.fulfill({status:state.status,json:state.status===200?{status:'success',message:'已删除班级'}:{detail:'预览后数据已变化，请重新核对。'}});
    }
    const body=merge?'<main style="max-width:1100px;margin:auto;padding:20px"><div id="offeringMergeLoading">加载中</div><div id="offeringMergeEmpty" hidden></div><div id="offeringMergeCandidates"></div></main><script type="module" src="/static/js/manage_offering_merge.js"></script>':
      '<main style="max-width:1100px;margin:auto;padding:20px"><button id="open">删除空班</button><div id="result"></div></main><script type="module">import {openTeachingDeleteConfirmation} from "/static/js/teaching_lifecycle_review.js"; document.getElementById("open").onclick=async()=>{ const result=await openTeachingDeleteConfirmation({kind:"class",resourceId:7}); document.getElementById("result").textContent=result?result.message:"已取消";};</script>';
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<body>${body}</body></html>`});
  });
  await page.goto('http://teaching-lifecycle.test/');
  if(!merge)await page.locator('#open').click();
  else await expect(page.locator('[data-merge-preview]')).toBeVisible();
  return {state,posts,errors,release};
}
async function fillDeclaration(page:Page) {
  await page.locator('[data-teaching-confirmation-text]').fill('待删空班');
  await page.locator('[data-teaching-delete-note]').fill('我已核对资源与关联内容');
  await page.locator('[data-teaching-delete-warning]').check();
}

test('normal delete requires manual declaration; stale failure preserves input and needs a fresh review', async({page})=>{
  const h=await mount(page);
  await expect(page.locator('[data-teaching-delete-submit]')).toBeDisabled();
  await expect(page.locator('[data-teaching-confirmation-text]')).toHaveValue('');
  expect(h.posts).toHaveLength(0);
  await fillDeclaration(page);
  await page.locator('[data-teaching-delete-submit]').click();
  await expect.poll(()=>h.posts.length).toBe(1);
  expect(h.posts[0]).toMatchObject({expected_review_hash:'a'.repeat(64),confirmation_text:'待删空班',accepted_warning_codes:['irreversible_root_delete']});
  await expect(page.locator('[data-teaching-delete-submit]')).toBeDisabled();
  await expect(page.locator('[data-teaching-delete-note]')).toHaveValue('我已核对资源与关联内容');
  h.state.revision='b'.repeat(64);h.state.status=200;
  await page.locator('[data-teaching-delete-refresh]').click();
  await expect(page.locator('[data-teaching-delete-warning]')).not.toBeChecked();
  await expect(page.locator('[data-teaching-confirmation-text]')).toHaveValue('待删空班');
  expect(h.posts).toHaveLength(1);
  await page.locator('[data-teaching-delete-warning]').check();
  await page.locator('[data-teaching-delete-submit]').click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('#result')).toHaveText('已删除班级');
  expect(h.posts[1].expected_review_hash).toBe('b'.repeat(64));
  expect(h.errors).toEqual([]);
});

test('inflight delete rejects duplicate/close; mobile cancel restores focus and fits viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const h=await mount(page);await fillDeclaration(page);h.state.hold=true;
  await page.locator('[data-teaching-delete-submit]').click();
  await expect.poll(()=>h.state.held).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.locator('[data-teaching-delete-submit]')).toBeDisabled();
  expect(h.posts).toHaveLength(1);
  h.release();
  await expect(page.locator('[data-teaching-delete-refresh]')).toBeEnabled();
  const dimensions=await page.getByRole('dialog').evaluate(el=>({width:el.getBoundingClientRect().width,viewport:innerWidth,body:document.documentElement.scrollWidth}));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport);
  await page.screenshot({path:'.codex-temp/teaching-lifecycle-mobile.png',fullPage:true});
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('#open')).toBeFocused();
  expect(h.errors).toEqual([]);
});

test('normal merge sends accepted hash and preserves typed name after uncertain/stale failure',async({page})=>{
  const h=await mount(page,true);
  await page.locator('[data-merge-preview]').click();
  await page.locator('[data-merge-confirm-input]').fill('一班');
  await page.locator('[data-merge-ack]').check();
  h.state.hold=true;
  await page.locator('[data-merge-execute]').click();
  await expect.poll(()=>h.state.held).toBe(true);
  await expect(page.locator('[data-merge-execute]')).toBeDisabled();
  expect(h.posts[0]).toMatchObject({expected_review_hash:'a'.repeat(64),acknowledged_irreversible:true,confirm_class_name:'一班'});
  h.release();
  await expect(page.locator('[data-merge-execute]')).toHaveText('请重新预检后确认');
  await expect(page.locator('[data-merge-confirm-input]')).toHaveValue('一班');
  await page.locator('input[type="radio"][value="31"]').check();
  await expect(page.locator('[data-merge-preview-result]')).toContainText('已更换主课堂');
  await expect(page.locator('[data-merge-execute]')).toHaveCount(0);
  expect(h.posts).toHaveLength(1);expect(h.errors).toEqual([]);
});

test('late merge preview cannot authorize a different classroom selection',async({page})=>{
  const h=await mount(page,true);h.state.hold=true;
  await page.locator('[data-merge-preview]').click();
  await expect.poll(()=>h.state.held).toBe(true);
  await page.locator('input[type="radio"][value="31"]').check();
  h.release();
  await expect(page.locator('[data-merge-preview-result]')).toContainText('已更换主课堂');
  await expect(page.locator('[data-merge-execute]')).toHaveCount(0);
  expect(h.posts).toHaveLength(0);expect(h.errors).toEqual([]);
});
