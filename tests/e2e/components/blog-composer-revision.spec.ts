import {test,expect} from '@playwright/test';
import fs from 'node:fs';

const source=fs.readFileSync('static/js/blog.js','utf8');
const controller=source.slice(source.indexOf('class BlogCenter {'),source.lastIndexOf("document.addEventListener('DOMContentLoaded'"));

test('restored blog draft retains its original revision and a conflict preserves content until explicit review',async({page})=>{
  const requests:any[]=[];let accept=false;
  await page.route('http://blog-cas.test/**',async route=>{
    if(route.request().method()==='PUT'){
      requests.push(route.request().postDataJSON());
      return route.fulfill({status:accept?200:409,json:accept?{status:'success',id:5}:{detail:'帖子已被更新，请重新读取并核对后再操作。'}});
    }
    return route.fulfill({contentType:'text/html',body:`<main data-blog-center><section data-blog-composer-modal hidden>
      <h2 data-blog-composer-title></h2><input data-blog-compose-title><textarea data-blog-compose-content></textarea><input data-blog-compose-tags>
      <input type="checkbox" data-blog-compose-comments><select data-blog-compose-visibility><option value="public">Public</option></select>
      <select data-blog-compose-section><option value="general">General</option></select><select data-blog-compose-class></select></section></main>`});
  });
  await page.goto('http://blog-cas.test/');
  await page.addScriptTag({content:`(()=>{const $=(q,root=document)=>root.querySelector(q);const $$=(q,root=document)=>[...root.querySelectorAll(q)];
    const uniqueMediaItems=()=>[];const showToast=message=>window.messages.push(message);window.messages=[];
    const api={put:async(url,data)=>{const response=await fetch(url,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.detail);return result;}};
    ${controller}
    const app=Object.create(BlogCenter.prototype);app.shell=document.querySelector('main');app.userIdentity='student:7';app.composeUserMap=new Map();
    app.state={editingPostId:null,uploadedImages:[],selectedUsers:[],currentSection:'general',currentView:'list'};
    for(const key of ['renderImagePreviews','renderSelectedUsers','updateVisibilityOptions','updateAuthorModeHint','setComposerMode','updateComposerMetrics','updateComposerSaveState','refreshCurrentList','loadDiscovery'])app[key]=()=>{};
    app.setSelectedAuthorMode=()=>{};app.getSelectedAuthorMode=()=> 'real_name';window.blogFixture=app;
    app.openComposer({id:5,updated_at:'version-1',title:'Original',content_md:'Original content'});
  })();`});
  const content=page.locator('[data-blog-compose-content]');
  await content.fill('My unfinished revision');
  await page.evaluate(()=>{const app=(window as any).blogFixture;app.saveComposerRecovery();app.closeComposer({force:true});app.openComposer({id:5,updated_at:'version-2',title:'New server title',content_md:'New server content'});});
  await expect(content).toHaveValue('My unfinished revision');
  await page.evaluate(()=>(window as any).blogFixture.savePost('published'));
  expect(requests[0].expected_updated_at).toBe('version-1');
  await expect(page.locator('[data-blog-composer-modal]')).toBeVisible();
  await expect(content).toHaveValue('My unfinished revision');
  expect(await page.evaluate(()=>(window as any).messages.at(-1))).toContain('帖子已被更新');
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('lanshare:blog-composer:student:7:5')!).expected_updated_at)).toBe('version-1');
  await page.evaluate(()=>{const app=(window as any).blogFixture;app.clearComposerRecovery(5);app.openComposer({id:5,updated_at:'version-2',title:'New server title',content_md:'New server content'});});
  await content.fill('Explicitly merged content');accept=true;
  await page.evaluate(()=>(window as any).blogFixture.savePost('published'));
  expect(requests[1].expected_updated_at).toBe('version-2');
  await expect(page.locator('[data-blog-composer-modal]')).toBeHidden();
  expect(await page.evaluate(()=>localStorage.getItem('lanshare:blog-composer:student:7:5'))).toBeNull();
});
