import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';

const repo = path.resolve(__dirname, '../../..');
const python = process.env.LQ_TEST_PYTHON ||
  (fs.existsSync(path.join(repo,'venv/Scripts/python.exe')) ? path.join(repo,'venv/Scripts/python.exe') : 'python');
const origin = 'https://classroom.test', content = 'https://isolated-game.test';
const example = path.join(repo,'docs/minigames/example');
const config = JSON.parse(fs.readFileSync(path.join(example,'config.json'),'utf-8'));
const definition = {config,rules:JSON.parse(fs.readFileSync(path.join(example,'contracts/rules.json'),'utf-8')),
  writeback:JSON.parse(fs.readFileSync(path.join(example,'contracts/writeback.json'),'utf-8'))};
const game = {id:'g1',slug:'beacon-relay',title:'航标接力',summary:'点亮航标',config,tags:[],min_tier:0,max_tier:10,
  minTier:0,maxTier:10,scope:'public',status:'listed',revision:4,currentVersionId:'v1',iconUrl:'/api/games/v1/games/g1/icon',
  authorName:'合成作者',allowRemix:true,versionCount:2,sourceVersionId:'v1',sourceUrl:'/api/games/v1/games/g1/source'};
function render(view:string, role:string, preference?:any){
  return execFileSync(python,[path.join(__dirname,'fixtures/minigame_templates.py')],{
    cwd:repo,input:JSON.stringify({page:view,role,game,preference}),encoding:'utf8',
    env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1'}
  });
}
async function fixture(page:Page,view:string,role='student',waitingForProbe=false,siteOrigin=origin){
  const writes:{path:string,body:any}[]=[], errors:string[]=[];
  let discovery={genres:[],tags:[],revision:0};
  const html=render(view,role,discovery);
  const me={playerId:'p1',displayName:'合成玩家',seat:1}, pref={revision:1,displayAlias:'',sharePlaying:false,audioMuted:false};
  let current:any=null,attr:any={...game},roomTries=0,uploaded=false,deleted=false,suggested=false,removed=false;
  let reviewStatus='manual', allowed=true, retired=false;
  page.on('pageerror',err=>errors.push(err.message));
  await page.route('**/*',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname.replace('/api/games/v1','');
    if(siteOrigin!==origin && u.origin===siteOrigin && p==='/admin/imports')return route.continue();
    const contentType=req.headers()['content-type'] || '';
    const body=contentType.includes('application/json')?req.postDataJSON():
      contentType.includes('multipart/form-data') ? Object.fromEntries(await new Response(req.postDataBuffer(),{headers:{'Content-Type':contentType}}).formData()) : {};
    const json=(data:any,status=200)=>route.fulfill({status,json:data});
    if(u.origin===content){
      if(p==='/main.html')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<html><body><button id="turn">点灯</button><p id="result"></p><script src="/sdk.js"></script><script>LanGame.ready().then(c=>document.querySelector("#result").textContent=c.me.displayName);document.querySelector("#turn").onclick=async()=>{const s=await LanGame.session.snapshot();const r=await LanGame.game.intent({type:"light",payload:{bearing:"east"}},{expectedRevision:s.revision,requestId:"unique-click-0001"});document.querySelector("#result").textContent="已点灯"}</script></body></html>'});
      if(p==='/sdk.js')return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(repo,'static/minigames/lan-game-sdk.v1.js'))});
      return route.abort();
    }
    if(u.origin!==siteOrigin)return route.abort();
    if(u.pathname.startsWith('/static/')){
      const f=path.resolve(repo,'.'+u.pathname);
      if(!f.startsWith(path.join(repo,'static')+path.sep)||!fs.existsSync(f))return route.fulfill({status:404,body:''});
      return route.fulfill({contentType:f.endsWith('.css')?'text/css':f.endsWith('.js')?'application/javascript':'application/octet-stream',body:fs.readFileSync(f)});
    }
    if(req.isNavigationRequest())return route.fulfill({contentType:'text/html; charset=utf-8',body:
      view==='hall' && discovery.revision ? render(view,role,discovery) : html});
    if(req.method()!=='GET'&&u.pathname.startsWith('/api/games/v1'))writes.push({path:p,body});
    if(p==='/catalog')return json({items:u.searchParams.get('q')==='无此游戏'?[]:[game],nextCursor:null});
    if(p==='/sidebar')return json({recent:[game],popular:[game],friends:[],recommended:[game]});
    if(p==='/me/preferences')return json(req.method()==='GET'?pref:Object.assign(pref,body));
    if(p==='/discovery-preferences'){
      if(req.method()==='PUT')discovery={genres:body.genres,tags:body.tags,revision:discovery.revision+1};
      return json(discovery);
    }
    if(p==='/me/activity')return json({solo:null,multiplayer:null});
    if(p==='/me/progress/g1')return json({privateSave:{sound:true,mapStyle:'paper'},privateRevision:1,rank:0});
    if(p==='/games/g1/launches')return json({leaseId:'l1',expiresAt:Date.now()+90000,contentEntryUrl:content+'/main.html',definition,versionId:'v1',me});
    if(p==='/games/g1/runs'){
      current={gameId:'g1',versionId:'v1',runId:'r1',mode:'solo',phase:'running',members:[me],me,publicState:{node:'atPier'},seq:1,revision:1,readOnly:false,controlEpoch:1,serverNow:Date.now()};
      return json(current);
    }
    if(p==='/sync')return json({snapshot:current,throughSeq:current?.seq||0,events:[]});
    if(p==='/runs/r1/intents'){current={...current,seq:2,revision:2};return json(current);}
    if(p==='/games/g1/rooms/join'){
      roomTries++; if(roomTries===1)return json({detail:{code:'ROOM_UNAVAILABLE',message:'暂时无法加入'}},404);
      current={...current,runId:null,roomId:'rm1',mode:'private',phase:'waiting',code:'M7K3',readOnly:false,targetSize:2};
      return json(current);
    }
    if(p==='/admin/games'&&req.method()==='POST')return json({game:{...body,id:'draft-new'}});
    if(p==='/admin/games')return json({items:[attr],nextCursor:null});
    if(p==='/admin/games/g1'&&req.method()==='PATCH'){attr={...attr,...body,revision:attr.revision+1};return json({game:attr});}
    if(p==='/admin/games/g1'&&req.method()==='DELETE'){deleted=true;return json({jobId:'del1',status:'queued'});}
    if(p==='/admin/jobs/del1')return json({id:'del1',status:deleted?'succeeded':'pending'});
    if(p==='/admin/imports'){uploaded=true;return json({gameId:'g1',jobId:'upload1',versionId:'v2',status:'queued'},202);}
    if(p==='/admin/games/g1/versions/v1/license'){allowed=body.allowRemix;return json({});}
    if(p==='/admin/games/g1/versions/v1/retire'){retired=true;attr={...attr,status:'unlisted'};return json({});}
    if(p==='/admin/games/g1/versions/v1' && req.method()==='DELETE'){removed=true;return json({jobId:'delete-v1'},202);}
    if(p.startsWith('/moderation/reviews')){
      const item={versionId:'v1',gameId:'g1',title:game.title,version:'1.0.0',status:reviewStatus,digest:'a'.repeat(64),
        reason:'需人工核对',categories:[],evidence:[{file:'js/game.js',reason:'需结合上下文确认'}],files:[{path:'main.html',size:251}]};
      if(p.endsWith('/decision')){reviewStatus=body.decision==='approve'?'approved':'rejected';return json({...item,status:reviewStatus});}
      return json(p==='/moderation/reviews'?{items:u.searchParams.get('status')===reviewStatus?[item]:[],hasMore:false}:item);
    }
    if(p==='/admin/games/g1/suggestions'){suggested=true;return json({jobId:'suggest1',status:'queued'});}
    if(p==='/admin/games/g1'){
      const versions:any[]=removed?[]:[{id:'v1',status:retired?'retired':'ready',releaseVersion:'1.0.0',allowRemix:allowed,
        sourceAuthor:'原作者',sourceTags:['原作标签'],review:role==='student'?{status:'approved',reason:'已通过'}:null}];
      if(role==='student')versions.unshift({id:'candidate',status:'reviewing',releaseVersion:'1.1.0',allowRemix:false,
        review:{status:'manual',reason:'等待人工确认'}});
      if(uploaded)versions.unshift({id:'v2',status:waitingForProbe?'validating':'ready',releaseVersion:waitingForProbe?'':'1.1.0'});
      const jobs:any[]=uploaded?[{id:'upload1',versionId:'v2',status:waitingForProbe?'waiting_probe':'succeeded',
        stage:waitingForProbe?'静态检查已通过，启用验收后自动继续':'隔离验收通过'}]:[];
      if(suggested)jobs.push({id:'suggest1',kind:'suggest',status:'succeeded',stage:'建议已生成',result:{purpose:'metadata',source:'rules',suggestion:'已按游戏包整理',summary:'沿着海图点灯。',tags:['方向']}});
      return json({game:attr,versions,jobs});
    }
    if(p.endsWith('/versions/v2/playtests'))return json({versionId:'v2',passed:body.passed});
    if(p.endsWith('/publish')){attr={...attr,currentVersionId:body.versionId,status:'listed',revision:attr.revision+1};return json({game:attr});}
    if(p.endsWith('/unlist')){attr={...attr,status:'unlisted',revision:attr.revision+1};return json({game:attr});}
    if(p.endsWith('/versions/v1/preview'))return json({url:'/games/beacon-relay?previewVersion=v1'});
    if(p.endsWith('/deletion-plans'))return json({confirmationText:'beacon-relay',planToken:'plan',impact:{versions:1,runs:2,messages:0,saves:1}});
    return json({});
  });
  const workspace=role==='student'?'/games/workshop':'/manage/library/games';
  const paths: Record<string,string>={hall:'/games',host:'/games/beacon-relay',manage:workspace,detail:workspace+'/g1',reviews:'/manage/library/games/reviews'};
  await page.goto(siteOrigin+paths[view]);
  if(view==='hall'||view==='host'){
    await expect(page.locator('script[src$="/auth.js"]')).toHaveCount(1);
    await expect(page.locator('script[src$="/lq/index.js"]')).toHaveCount(1);
    await expect(page.locator('script[src*="cultivation_identity"],script[src*="cultivation_emblems"],[data-lanshare-island="message-center-sync"]')).toHaveCount(0);
  }
  return {writes,errors};
}

// Chromium itself creates the FileList and directory entries from these filesystem paths.
async function dropFromDisk(page: Page, files: string[]) {
  const zone = page.locator('[data-dropzone]:visible');
  await zone.scrollIntoViewIfNeeded();
  const box = await zone.boundingBox();
  if (!box) throw Error('Upload target has no visible bounds');
  const session = await page.context().newCDPSession(page);
  try {
    for (const type of ['dragEnter','dragOver','drop'] as const) {
      await session.send('Input.dispatchDragEvent', {
        type, x: box.x + box.width / 2, y: box.y + 12,
        data: { items: [], files, dragOperationsMask: 1 },
      });
    }
  } finally {
    await session.detach();
  }
}

const sampleArchive = path.join(repo,'docs/minigames/downloads/star-map.zip');

// CDP omits disk-backed bytes from intercepted multipart requests. Inspect the HTTP body itself.
const uploadTest = test.extend<{uploads:{origin:string, forms:FormData[]}}>({
  uploads: async ({}, use) => {
    const forms: FormData[] = [];
    const server = createServer(async (req,res) => {
      if (req.url !== '/api/games/v1/admin/imports' || req.method !== 'POST') {
        res.writeHead(404).end();
        return;
      }
      try {
        const chunks: Buffer[] = [];
        for await (const chunk of req) chunks.push(chunk);
        forms.push(await new Response(Buffer.concat(chunks),{headers:{'Content-Type':req.headers['content-type']!}}).formData());
        res.writeHead(202,{'Content-Type':'application/json'}).end(JSON.stringify({gameId:'g1',jobId:'upload1',versionId:'v2',status:'queued'}));
      } catch {
        res.writeHead(400).end();
      }
    });
    await new Promise<void>((resolve,reject) => server.once('error',reject).listen(0,'127.0.0.1',resolve));
    try {
      await use({origin:'http://127.0.0.1:'+(server.address() as AddressInfo).port,forms});
    } finally {
      await new Promise<void>(resolve => {server.close(() => resolve());server.closeAllConnections();});
    }
  },
});

uploadTest('拖入 ZIP 后可取消、重新选择并手动提交同一份原始文件', async ({page,uploads}) => {
  const {errors} = await fixture(page,'manage','teacher',false,uploads.origin);
  const open = page.getByRole('button',{name:'上传新游戏'});
  await open.click();
  await dropFromDisk(page,[sampleArchive]);
  const dialog = page.getByRole('dialog').filter({has:page.locator('[data-version-upload]')});
  await expect(dialog.locator('[data-file-meta]')).toContainText('star-map.zip');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(uploads.forms).toHaveLength(0);
  await open.click();
  await expect(dialog.locator('[data-file-meta]')).not.toContainText('star-map.zip');
  await dropFromDisk(page,[path.join(example,'config.json')]);
  await expect(dialog.locator('[data-file-meta]')).toContainText(/ZIP|目录|文件夹|main\.html/i);
  await dropFromDisk(page,[sampleArchive]);
  await expect(dialog.locator('[data-file-meta]')).toContainText('star-map.zip');
  expect(uploads.forms).toHaveLength(0);
  await dialog.locator('form').evaluate((form: HTMLFormElement) => {
    form.requestSubmit();
    form.requestSubmit();
  });
  await expect(page).toHaveURL(/\/games\/g1$/);
  expect(uploads.forms).toHaveLength(1);
  const file = uploads.forms[0].get('file') as File;
  expect(Buffer.from(await file.arrayBuffer()).equals(fs.readFileSync(sampleArchive))).toBe(true);
  expect(errors).toEqual([]);
});

uploadTest('续版拖入完整文件夹保留嵌套路径、跨页读取并通过正式包校验', async ({page,uploads}) => {
  const {errors} = await fixture(page,'detail','student',false,uploads.origin);
  const scratch = fs.mkdtempDisposableSync(path.join(os.tmpdir(),'minigame-e2e-'));
  try {
    const folder = path.join(scratch.path,'星图');
    execFileSync(python,['-m','zipfile','-e',sampleArchive,folder]);
    const levels = path.join(folder,'static','关卡');
    fs.mkdirSync(levels);
    for (let i = 0; i < 103; i++) fs.writeFileSync(path.join(levels,`图-${i}.json`),JSON.stringify({level:i}));
    await dropFromDisk(page,[folder]);
    await expect(page.locator('[data-file-meta]')).toContainText('星图');
    expect(uploads.forms).toHaveLength(0);
    await page.getByRole('button',{name:'上传并验收'}).click();
    await expect.poll(() => uploads.forms.length).toBe(1);
    const body = uploads.forms[0];
    expect(body.get('targetGameId')).toBe('g1');
    expect(body.get('expectedRevision')).toBe('4');
    const archive = path.join(scratch.path,'received.zip');
    fs.writeFileSync(archive,Buffer.from(await (body.get('file') as File).arrayBuffer()));
    const report = execFileSync(python,['tools/minigame_pack.py','validate',archive],
      {cwd:repo,encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}});
    expect(JSON.parse(report)).toMatchObject({staticValidation:'passed',gameKey:'star-map',files:112});
    const parity = [
      'import pathlib,sys,zipfile',
      'root=pathlib.Path(sys.argv[2])',
      'with zipfile.ZipFile(sys.argv[1]) as archive:',
      '    for file in root.rglob("*"):',
      '        if file.is_file(): assert archive.read(file.relative_to(root).as_posix())==file.read_bytes()',
    ].join('\n');
    execFileSync(python,['-c',parity,archive,folder]);
    expect(errors).toEqual([]);
  } finally {
    scratch.remove();
  }
});

uploadTest('上传弹窗可粘贴游戏文件并保留标签框的普通文本粘贴', async ({page,uploads},info) => {
  await page.setViewportSize({width:320,height:760});
  const {errors} = await fixture(page,'manage','student',false,uploads.origin);
  await page.getByRole('button',{name:'上传新游戏'}).click();
  const dialog = page.getByRole('dialog');
  const tags = dialog.getByLabel('作品标签（用逗号分隔）');
  await tags.focus();
  // Synthetic clipboard data exercises the browser's paste event without using the operator's clipboard.
  const textConsumed = await tags.evaluate(input => {
    const transfer = new DataTransfer();
    transfer.setData('text/plain','像素风');
    const event = new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true});
    input.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(textConsumed).toBe(false);
  const original = fs.readFileSync(sampleArchive);
  const fileConsumed = await tags.evaluate((input,base64) => {
    const transfer = new DataTransfer();
    const bytes = Uint8Array.from(atob(base64),c => c.charCodeAt(0));
    transfer.items.add(new File([bytes],'粘贴游戏.zip',{type:'application/zip'}));
    const event = new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true});
    input.dispatchEvent(event);
    return event.defaultPrevented;
  },original.toString('base64'));
  expect(fileConsumed).toBe(true);
  await expect(dialog.locator('[data-file-meta]')).toContainText('粘贴游戏.zip');
  expect(uploads.forms).toHaveLength(0);
  const submit = dialog.getByRole('button',{name:'上传并验收'});
  await submit.scrollIntoViewIfNeeded();
  await expect(submit).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await page.screenshot({path:info.outputPath('文件粘贴上传-320.png'),animations:'disabled'});
  await submit.click();
  await expect(page).toHaveURL(/\/games\/workshop\/g1$/);
  expect(uploads.forms).toHaveLength(1);
  expect(Buffer.from(await (uploads.forms[0].get('file') as File).arrayBuffer()).equals(original)).toBe(true);
  expect(errors).toEqual([]);
});

test('游戏大厅布局、原生链接、搜索和游玩偏好',async({page},info)=>{
  const {writes}=await fixture(page,'hall');
  const card=page.locator('[data-game-tile]');await expect(card).toHaveAttribute('target','_blank');
  for(const width of [1920,1024,390,320]){
    await page.setViewportSize({width,height:900});
    const layout=await page.evaluate(()=>{const hall=document.querySelector('.game-hall')!,aside=document.querySelector('.game-aside')!,left=document.querySelector('#game-directory')!;return {ratio:aside.clientWidth/(aside.clientWidth+left.clientWidth),grid:getComputedStyle(hall).gridTemplateColumns.split(' ').length,scroll:document.documentElement.scrollWidth};});
    expect(layout.scroll).toBeLessThanOrEqual(width);if(width>=960){expect(layout.grid).toBe(2);expect(layout.ratio).toBeGreaterThan(.25);expect(layout.ratio).toBeLessThan(.334);}
    else expect(layout.grid).toBe(1);
    if(width===1920){
      await page.evaluate(()=>{document.documentElement.dataset.lqGlass='tinted';document.documentElement.dataset.lqTier='A';});
      await card.hover({position:{x:17,y:15}});await expect(card).toHaveAttribute('data-halo-active','');
      await expect(card.locator('.game-halo')).toHaveCSS('opacity','1');
      await page.mouse.move(1,1);await expect(card).not.toHaveAttribute('data-halo-active','');
    }
    if(width===1920||width===390)await page.screenshot({path:info.outputPath('大厅-'+width+'.png')});
  }
  await page.getByRole('button',{name:'游玩设置'}).click();
  await page.getByLabel('游戏昵称').fill('远山');await page.getByRole('button',{name:'保存设置'}).click();
  await expect.poll(()=>writes.filter(x=>x.path==='/me/preferences').length).toBe(1);
  await page.getByRole('searchbox').fill('无此游戏');await expect(page.locator('[data-grid-empty]')).toBeVisible();
});
test('真实隔离 SDK 单人开局与动作，错码保留原局后转队',async({page})=>{
  const {writes}=await fixture(page,'host');
  await expect(page.getByRole('link',{name:'下载源码'})).toHaveAttribute('href','/api/games/v1/games/g1/source');
  await expect(page.getByRole('link',{name:'改编此版本'})).toHaveAttribute('href','/games/workshop?sourceVersionId=v1');
  const gameFrame=page.frameLocator('.game-frame-box iframe');await expect(gameFrame.locator('#result')).toHaveText('合成玩家');
  await gameFrame.getByRole('button',{name:'点灯'}).click();await expect(gameFrame.locator('#result')).toHaveText('已点灯');
  expect(writes.find(x=>x.path.endsWith('/intents'))?.body.intent.type).toBe('light');
  await page.getByRole('button',{name:'加入队伍',exact:true}).click();
  await page.getByLabel('四位队伍码').fill('k7-m3');await page.getByRole('button',{name:'加入',exact:true}).click();
  await expect(page.getByText('暂时无法加入',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'加入',exact:true}).click();await expect(page.locator('[data-code-text]')).toHaveText('M7K3');
  expect(writes.filter(x=>x.path.endsWith('/rooms/join')).every(x=>x.body.fromSolo.runId==='r1')).toBeTruthy();
});
test('教师页范围表单、隔离预览、永久删除前可以取消',async({page},info)=>{
  const {writes}=await fixture(page,'detail','teacher');
  await expect(page.getByLabel('展示名称')).toHaveCount(0);
  await page.getByLabel('简介',{exact:true}).fill('航标接力新版');await page.getByRole('button',{name:'保存属性'}).click();
  await expect.poll(()=>writes.filter(x=>x.path==='/admin/games/g1'&&x.body.summary==='航标接力新版').length).toBe(1);
  await page.getByRole('button',{name:'教师预览'}).click();await expect(page.getByRole('link',{name:'打开隔离预览'})).toHaveAttribute('target','_blank');
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'彻底删除游戏',exact:true}).click();
  await expect(page.getByLabel('输入 beacon-relay 确认')).toBeVisible();await page.keyboard.press('Escape');
  expect(writes.filter(x=>x.path.endsWith('/deletion-plans'))).toHaveLength(1);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:info.outputPath('教师属性-390.png')});
});
test('教师管理列表筛选及直接上传游戏包',async({page})=>{
  const {writes}=await fixture(page,'manage','teacher');
  await page.getByLabel('搜索名称、标识或标签').fill('航标');await page.getByRole('button',{name:'筛选',exact:true}).click();
  await expect(page).toHaveURL(/q=%E8%88%AA%E6%A0%87/);
  await expect(page.locator('[data-admin-list]').getByRole('link',{name:'航标接力'})).toBeVisible();
  await page.getByRole('button',{name:'上传新游戏'}).click();await expect(page.getByLabel('游戏名称')).toHaveCount(0);
  await page.getByLabel('独立 HTML 游戏包（ZIP）').setInputFiles({name:'artifact.zip',mimeType:'application/zip',buffer:Buffer.from('fixture')});
  await page.getByRole('button',{name:'上传并验收'}).click();await expect(page).toHaveURL(/games\/g1$/);
  expect(writes.find(x=>x.path==='/admin/imports')?.body.allowRemix).toBe('true');
});

test('教师可在桌面和窄屏阅读开发规范并下载开发包和 Markdown',async({page},info)=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  await fixture(page,'manage','teacher');
  const guidePath='/manage/library/games/author-guide';
  await page.route(origin+guidePath,route=>route.fulfill({
    contentType:'text/html; charset=utf-8',body:render('author_guide','teacher'),
  }));
  for(const [suffix,filename] of [['/download','author-kit.zip'],['/markdown','author-guide.md']]){
    await page.route(origin+guidePath+suffix,route=>route.fulfill({
      headers:{'Content-Disposition':`attachment; filename="${filename}"`},
      body:Buffer.from('synthetic download'),
    }));
  }
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:844});
    const section=page.getByRole('region',{name:'小游戏开发规范'});
    const read=section.getByRole('link',{name:'在线阅读'});
    await expect(read).toBeInViewport();
    await expect(section.getByRole('link',{name:'下载开发包'})).toHaveAttribute('href',guidePath+'/download');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    if(width===390){
      await page.screenshot({path:info.outputPath('开发规范入口-390.png'),animations:'disabled'});
      await page.locator('[data-lq-pane-open="actions"]').click();
      await expect(page.getByRole('button',{name:'上传新游戏'})).toBeVisible();
      await page.keyboard.press('Escape');
    }
    await read.click();
    const doc=page.getByRole('article',{name:'小游戏开发规范全文'});
    await expect(doc.getByRole('heading',{level:1})).toContainText('小游戏');
    await expect(doc.locator('pre').filter({hasText:'main.html'}).first()).toBeAttached();
    if(width!==320)await page.screenshot({path:info.outputPath(`规范阅读-${width}.png`),animations:'disabled'});
    await doc.locator('summary').focus();
    await page.keyboard.press('Enter');
    const firstSection = doc.locator('nav a[href="#section-1"]');
    await expect(firstSection).toBeVisible();
    const heading = await firstSection.textContent();
    expect(heading).toBeTruthy();
    await firstSection.click();
    await expect(page).toHaveURL(/#section-1$/);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(doc.locator('h2#section-1')).toHaveText(heading!);
    await expect(doc.locator('h2#section-1')).toBeInViewport();
    if(width===390){
      for(const [label,ext] of [['下载开发包（ZIP）','.zip'],['下载规范（Markdown）','.md']]){
        const downloaded=page.waitForEvent('download');
        await page.getByRole('link',{name:label,exact:true}).click();
        expect((await downloaded).suggestedFilename().endsWith(ext)).toBe(true);
      }
    }
    await page.goto(origin+'/manage/library/games');
  }
});
test('教师上传验收、登记试玩、发布、下架和彻底删除',async({page})=>{
  const {writes}=await fixture(page,'detail','teacher');
  await page.getByLabel('独立 HTML 游戏包（ZIP）').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:Buffer.from('synthetic')});
  await page.getByRole('button',{name:'上传并验收'}).click();await expect(page.getByText('隔离验收通过')).toBeVisible();
  const candidate=page.locator('[data-game-versions] li').filter({hasText:'1.1.0'});
  await candidate.getByRole('button',{name:'确认已试玩'}).click();await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect.poll(()=>writes.filter(x=>x.path.endsWith('/playtests')).length).toBe(1);
  await candidate.getByRole('button',{name:'发布此版本'}).click();await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(candidate.getByText('当前版本')).toBeVisible();
  await page.getByRole('button',{name:'下架游戏'}).click();await page.getByRole('button',{name:'确认执行'}).click();
  await expect(page.locator('[data-detail-status]')).toContainText('已下架');
  await page.getByRole('button',{name:'彻底删除游戏',exact:true}).click();
  await page.getByLabel('输入 beacon-relay 确认').fill('beacon-relay');await page.getByRole('button',{name:'确认彻底删除'}).click();
  await expect(page.locator('[data-game-notice]')).toHaveText('游戏已彻底删除');
});
test('教师手动生成建议并核对属性草稿',async({page})=>{
  const {writes}=await fixture(page,'detail','teacher');
  await page.getByRole('button',{name:'拟写简介与标签'}).click();await page.getByRole('button',{name:'查看建议'}).click();
  await page.getByRole('button',{name:'采用到属性草稿'}).click();await page.keyboard.press('Escape');
  await expect(page.getByLabel('简介',{exact:true})).toHaveValue('沿着海图点灯。');
  expect(writes.filter(x=>x.path==='/admin/games/g1')).toHaveLength(0);
  await page.getByRole('button',{name:'保存属性'}).click();await expect.poll(()=>writes.filter(x=>x.path==='/admin/games/g1').length).toBe(1);
});

test('执行器等待状态在刷新后可见且候选版本无法提前发布',async({page})=>{
  await fixture(page,'detail','teacher',true);
  await page.setViewportSize({width:390,height:844});
  await page.getByLabel('独立 HTML 游戏包（ZIP）').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:Buffer.from('synthetic')});
  await page.getByRole('button',{name:'上传并验收'}).click();
  await expect(page.getByText('待浏览器验收',{exact:true})).toBeVisible();
  await page.reload();
  await expect(page.getByText('静态检查已通过，启用验收后自动继续')).toBeVisible();
  const candidate=page.locator('[data-game-versions] li').filter({hasText:'候选版'});
  await expect(candidate.getByRole('button',{name:'验收报告'})).toBeVisible();
  await expect(candidate.getByRole('button',{name:'发布此版本'})).toHaveCount(0);
  await expect(candidate.getByRole('button',{name:'教师预览'})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('大厅按偏好筛选并在新访问时还原，学生创作入口独立',async({page})=>{
  const {writes} = await fixture(page,'hall');
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('link',{name:'我的作品'})).toHaveAttribute('href','/games/workshop');
  await page.getByRole('button',{name:'类型与标签'}).click();
  await page.getByLabel('冒险',{exact:true}).check();
  await page.getByRole('button',{name:'像素风',exact:true}).click();
  await page.getByRole('button',{name:'确认筛选'}).click();
  await expect(page).toHaveURL(/genres=adventure/);
  expect(writes.find(x=>x.path==='/discovery-preferences')?.body).toEqual({genres:['adventure'],tags:['像素风'],expectedRevision:0});
  await page.goto(origin+'/games');
  await expect(page.locator('[data-discovery-summary]')).toHaveText('冒险 · #像素风');
  await page.getByRole('button',{name:'类型与标签'}).click();
  await page.getByRole('button',{name:'清除选择'}).click();
  await page.getByLabel('记住此次选择，下次进入大厅自动筛选').uncheck();
  await page.getByRole('button',{name:'确认筛选'}).click();
  await expect(page).toHaveURL(/genres=&tags=/);
  expect(writes.filter(x=>x.path==='/discovery-preferences')).toHaveLength(1);
});

test('学生从原作进入改编上传，持有原版标识并能撤选许可',async({page},info)=>{
  const {writes}=await fixture(page,'manage','student');
  await page.setViewportSize({width:390,height:844});
  await page.goto(origin+'/games/workshop?sourceVersionId=v1');
  const dialog=page.getByRole('dialog');
  await expect(dialog.getByLabel('原作版本 ID（改编时填写）')).toHaveValue('v1');
  await expect(dialog.getByLabel('允许他人下载完整源码并二次开发')).toBeChecked();
  await expect(page.locator('.manage-sidebar')).toHaveCount(0);
  await dialog.getByLabel('允许他人下载完整源码并二次开发').uncheck();
  await dialog.getByLabel('作品标签（用逗号分隔）').fill('3D， 合作');
  await dialog.getByLabel('独立 HTML 游戏包（ZIP）').setInputFiles({name:'fork.zip',mimeType:'application/zip',buffer:Buffer.from('fixture')});
  await page.screenshot({path:info.outputPath('学生改编上传-390.png'),animations:'disabled'});
  await dialog.getByRole('button',{name:'上传并验收'}).click();
  await expect(page).toHaveURL(/\/games\/workshop\/g1$/);
  const request=writes.find(x=>x.path==='/admin/imports')!;
  expect(request.body.sourceVersionId).toBe('v1');
  expect(request.body.allowRemix).toBe('false');
  expect(JSON.parse(request.body.tags)).toEqual(['3D','合作']);
});

test('学生工作区按版本展示署名与审核，可管理许可、下架及删除',async({page},info)=>{
  const {writes}=await fixture(page,'detail','student');
  await page.setViewportSize({width:320,height:844});
  const version=page.locator('[data-game-versions] li').filter({hasText:'1.0.0'});
  await expect(page.locator('[data-game-versions] li').first()).toContainText('待超管审核');
  await expect(version).toContainText('内容审核通过');
  await expect(version).toContainText('改编自 原作者');
  await expect(version).toContainText('#原作标签');
  await expect(version.getByRole('button',{name:'发布此版本'})).toHaveCount(0);
  await expect(page.getByLabel('教师精选')).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await version.scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath('学生版本管理-320.png'),animations:'disabled'});
  await version.getByRole('button',{name:'关闭二开许可'}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(version.getByRole('button',{name:'允许二次开发'})).toBeVisible();
  await version.getByRole('button',{name:'下架此版本'}).click();
  await page.getByRole('button',{name:'确认执行',exact:true}).click();
  await expect(version.getByRole('button',{name:'下架此版本'})).toHaveCount(0);
  await version.getByRole('button',{name:'删除此版本'}).click();
  await page.getByRole('button',{name:'确认执行',exact:true}).click();
  await expect(version).toHaveCount(0);
  expect(writes.filter(x=>x.path.includes('/versions/v1'))).toHaveLength(3);
});

test('超管从审核队列查看文件并仅对当前摘要提交决定',async({page},info)=>{
  const {writes}=await fixture(page,'reviews','teacher');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'查看审核'}).click();
  await page.getByText('包内文件（1）').click();
  await expect(page.getByText('main.html · 251 B')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('超管审核-390.png'),animations:'disabled'});
  await page.getByRole('button',{name:'退回作者',exact:true}).click();
  await page.getByLabel('审核意见').fill('请替换此段文本');
  await page.getByRole('button',{name:'确认退回作者'}).click();
  await expect.poll(()=>writes.filter(x=>x.path.endsWith('/decision')).length).toBe(1);
  expect(writes.find(x=>x.path.endsWith('/decision'))?.body).toEqual({decision:'reject',reason:'请替换此段文本',expectedDigest:'a'.repeat(64)});
  await expect(page.getByText('已保存审核决定')).toBeAttached();
});
