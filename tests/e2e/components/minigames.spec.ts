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
  const sidebar = view === 'hall' ? {popular:[{...game, playerCount:120, recentPlayerCount:13, heatScore:138}]} : undefined;
  return execFileSync(python,[path.join(__dirname,'fixtures/minigame_templates.py')],{
    cwd:repo,input:JSON.stringify({page:view,role,game,preference,sidebar}),encoding:'utf8',
    env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1'}
  });
}
async function fixture(page:Page,view:string,role='student',waitingForProbe=false,siteOrigin=origin){
  const writes:{path:string,body:any}[]=[], errors:string[]=[];
  let discovery={genres:[],tags:[],revision:0};
  const html=render(view,role,discovery);
  const me={playerId:'p1',displayName:'合成玩家',seat:1}, pref={revision:1,displayAlias:'',sharePlaying:false,audioMuted:false};
  let submittedV2 = false;
  let current:any=null,attr:any={...game},roomTries=0,uploaded=false,deleted=false,suggested=false,removed=false;
  let reviewStatus='manual', reviewReason='AI 审核存在不确定项，请超管人工复核：需结合上下文确认', allowed=true, retired=false, playtestedV2=false, resubmitted=false, autoAiReview=false;
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
    if(p==='/sidebar')return json({recent:[game],popular:[{...game, playerCount:121, recentPlayerCount:14, heatScore:148}],friends:[],recommended:[game]});
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
    if(p==='/admin/games/g1/versions/v1/retire'){retired=true;attr={...attr,status:'unlisted',revision:attr.revision+1};return json({game:attr});}
    if(p==='/admin/games/g1/versions/v2/submit-review'){submittedV2=true;return json({game:attr});}
    if(p==='/admin/games/g1/versions/v1/submit-review'){resubmitted=true;retired=false;attr={...attr,revision:attr.revision+1};return json({game:attr});}
    if(p==='/admin/games/g1/versions/v1' && req.method()==='DELETE'){removed=true;return json({jobId:'delete-v1'},202);}
    if(p.startsWith('/moderation/reviews')){
      if(p==='/moderation/reviews/settings'){
        if(req.method()==='PATCH')autoAiReview=Boolean(body.autoAiReview);
        return json({autoAiReview});
      }
      const tree={
        id:1,parent_id:null,name:game.title+' (1.0.0)',node_type:'folder',material_path:'/',rel_path:'',
        document_kind:'html_package',kind_label:'游戏包目录',kind_color:'#4f46e5',preview_mode:'folder',
        file_size:621,child_count:3,can_manage:false,editable:false,download_allowed:false,ai_findings:[],ai_finding_count:1,
        children:[
          {
            id:2,parent_id:1,name:'js',node_type:'folder',material_path:'/js',rel_path:'js',
            document_kind:'folder',kind_label:'文件夹',kind_color:'#0ea5e9',preview_mode:'folder',
            file_size:320,child_count:1,can_manage:false,editable:false,download_allowed:false,ai_findings:[],ai_finding_count:1,
            children:[
              {
                id:3,parent_id:2,name:'game.js',node_type:'file',material_path:'/js/game.js',rel_path:'js/game.js',
                file_ext:'js',mime_type:'text/javascript',file_size:320,sha256:'b'.repeat(64),
                document_kind:'code',kind_label:'源码',kind_color:'#0284c7',preview_mode:'text',
                preview_content_url:'/api/games/v1/moderation/reviews/v1/file-content?path=js%2Fgame.js',
                download_url:'/api/games/v1/moderation/reviews/v1/file?path=js%2Fgame.js&download=1',
                can_manage:false,editable:false,download_allowed:true,ai_findings:['需结合上下文确认'],ai_finding_count:1,
              },
            ],
          },
          {
            id:4,parent_id:1,name:'main.html',node_type:'file',material_path:'/main.html',rel_path:'main.html',
            file_ext:'html',mime_type:'text/html',file_size:251,sha256:'c'.repeat(64),
            document_kind:'html_page',kind_label:'HTML 源码',kind_color:'#d97706',preview_mode:'text',
            preview_content_url:'/api/games/v1/moderation/reviews/v1/file-content?path=main.html',
            download_url:'/api/games/v1/moderation/reviews/v1/file?path=main.html&download=1',
            can_manage:false,editable:false,download_allowed:true,ai_findings:[],ai_finding_count:0,
          },
          {
            id:5,parent_id:1,name:'config.json',node_type:'file',material_path:'/config.json',rel_path:'config.json',
            file_ext:'json',mime_type:'application/json',file_size:50,sha256:'d'.repeat(64),
            document_kind:'code',kind_label:'源码',kind_color:'#0284c7',preview_mode:'text',
            preview_content_url:'/api/games/v1/moderation/reviews/v1/file-content?path=config.json',
            download_url:'/api/games/v1/moderation/reviews/v1/file?path=config.json&download=1',
            can_manage:false,editable:false,download_allowed:true,ai_findings:[],ai_finding_count:0,
          },
        ],
      };
      const item={
        versionId:'v1',gameId:'g1',slug:game.slug,gameKey:game.slug,title:game.title,summary:game.summary,
        version:'1.0.0',status:reviewStatus,versionStatus:'reviewing',gameStatus:'draft',digest:'a'.repeat(64),
        reason:reviewReason,
        aiReason:'需结合上下文确认',decider:reviewStatus==='manual'?'ai':'teacher:97',
        categories:['sexual'],aiVerdict:'uncertain',findingCount:1,authorName:'合成学生',ownerRole:'student',
        fileCount:3,totalBytes:621,tags:['像素风'],allowRemix:true,config,
        aiReport:{
          verdict:'uncertain',
          summary:'已核查入口、脚本与配置，发现一段剧情台词需人工结合上下文确认。',
          adultElementsNote:'含少量轻度浪漫对白，整体在常规游戏允许范围内。',
          reviewAdvice:'请打开 js/game.js 查看对白上下文后决定发布或退回。',
          categoryStates:{sexual:'uncertain',political:'clear',drugs:'clear',severe_harm:'clear'},
          checkedCount:3,totalCount:3,unreadCount:0,checkedAt:Date.now(),
        },
        evidence:[{file:'js/game.js',reason:'需结合上下文确认'}],
        files:[{path:'js/game.js',size:320,mime:'text/javascript'},{path:'main.html',size:251,mime:'text/html'}],
        treeUrl:'/api/games/v1/moderation/reviews/v1/tree',
        packageUrl:'/api/games/v1/moderation/reviews/v1/package',
        canPreview:true,manageUrl:'/manage/library/games/g1?version=v1',
        createdAt:Date.now()-60000,updatedAt:Date.now(),
      };
      if(p.endsWith('/tree'))return json({tree,selected_id:1,stats:{folder_count:1,file_count:3,total_size:621}});
      if(p.endsWith('/file-content')){
        const fp=u.searchParams.get('path')||'';
        const src=fp==='js/game.js'?'// 合成游戏脚本\nexport function startGame() { return "beacon"; }\n':'<!doctype html><title>航标接力</title>';
        return json({content:src,encoding:'utf-8',truncated:false,size:src.length,path:fp,mime:'text/javascript'});
      }
      if(p.endsWith('/ai-review')){reviewStatus='queued';reviewReason='超管已提交 AI 风险查验，正在排队处理…';return json({...item,status:'queued',reason:reviewReason});}
      if(p.endsWith('/preview'))return json({url:'/games/beacon-relay?previewVersion=v1',versionId:'v1'});
      if(p.endsWith('/decision')){reviewStatus=body.decision==='approve'?'approved':'rejected';reviewReason=body.reason;return json({...item,status:reviewStatus,reason:reviewReason});}
      const counts={
        manual:reviewStatus==='manual'?1:0,
        aiPending:reviewStatus==='queued'||reviewStatus==='running'?1:0,
        approved:reviewStatus==='approved'?1:0,
        rejected:reviewStatus==='rejected'?1:0,
        cancelled:0,total:1,
      };
      const st=u.searchParams.get('status')||'manual';
      const matches=st==='all'||st===reviewStatus||(st==='ai_pending'&&(reviewStatus==='queued'||reviewStatus==='running'));
      return json(p==='/moderation/reviews'?{items:matches?[item]:[],hasMore:false,counts,settings:{autoAiReview}}:item);
    }
    if(p==='/admin/games/g1/suggestions'){suggested=true;return json({jobId:'suggest1',status:'queued'});}
    if(p==='/admin/games/g1'){
      const v1Status=resubmitted?'reviewing':retired?'retired':'ready';
      const v1Review=role==='student'?(resubmitted?{status:'manual',reason:''}:{status:'approved',reason:'已通过'}):null;
      const versions:any[]=removed?[]:[{id:'v1',status:v1Status,releaseVersion:'1.0.0',digest:'a'.repeat(64),allowRemix:allowed,playtested:true,
        authorName:'合成作者',config,sourceAuthor:'原作者',sourceTags:['原作标签'],review:v1Review}];
      if(role==='super')versions.push({id:'archive',status:'retired',releaseVersion:'0.9.0',digest:'b'.repeat(64),config});
      if(role==='student')versions.unshift({id:'candidate',status:'reviewing',releaseVersion:'1.1.0',allowRemix:false,
        authorName:'合成作者',config,review:{status:'manual',reason:'等待人工确认'}});
      if(uploaded)versions.unshift({id:'v2',status:waitingForProbe?'validating':submittedV2?'reviewing':'ready',review:submittedV2?{status:'manual'}:null,previewed:true,releaseVersion:waitingForProbe?'':'1.1.0',
        allowRemix:true,playtested:playtestedV2,authorName:'合成作者',config});
      const jobs:any[]=uploaded?[{id:'upload1',kind:'validate',versionId:'v2',status:waitingForProbe?'waiting_probe':'succeeded',
        stage:waitingForProbe?'静态检查已通过，启用验收后自动继续':'隔离验收通过'}]:[];
      if(suggested)jobs.push({id:'suggest1',kind:'suggest',status:'succeeded',stage:'建议已生成',result:{purpose:'metadata',source:'rules',suggestion:'已按游戏包整理',summary:'沿着海图点灯。',tags:['方向']}});
      const relatedGames=[{id:'g-remix',slug:'beacon-remix',gameKey:'beacon-remix',title:'航标接力·夜航版',status:'listed',versionCount:2,relation:'remix'}];
      return json({game:attr,versions,relatedGames,jobs});
    }
    if(p.endsWith('/versions/v2/playtests')){playtestedV2=true;return json({versionId:'v2',passed:body.passed});}
    if(p.endsWith('/publish')){attr={...attr,currentVersionId:body.versionId,status:'listed',revision:attr.revision+1};return json({game:attr});}
    if(p.endsWith('/unlist')){attr={...attr,status:'unlisted',revision:attr.revision+1};return json({game:attr});}
    if(p.endsWith('/versions/v1/preview'))return json({url:'/games/beacon-relay?previewVersion=v1'});
    if(p.endsWith('/deletion-plans'))return json({confirmationText:'beacon-relay',planToken:'plan',impact:{versions:1,runs:2,messages:0,saves:1}});
    return json({});
  });
  const workspace=role==='student'?'/games/workshop':'/manage/library/games';
  const paths: Record<string,string>={classroom:'/classrooms/123',hall:'/games',host:'/games/beacon-relay',manage:workspace,detail:workspace+'/g1',reviews:'/manage/library/games/reviews'};
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
    await expect(page.locator('[data-dropzone]:visible')).toHaveCount(0);
    await page.getByRole('button',{name:'上传新版本'}).click();
    await dropFromDisk(page,[folder]);
    const dialog = page.getByRole('dialog').filter({has:page.locator('[data-version-upload]')});
    await expect(dialog.locator('[data-file-meta]')).toContainText('星图');
    expect(uploads.forms).toHaveLength(0);
    await dialog.getByRole('button',{name:'上传并验收'}).click();
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
test('热门游戏名称后的热度、累计人数与近期人数在首屏和刷新后一致呈现', async ({page}, info) => {
  await page.setViewportSize({width:320,height:720});
  await fixture(page,'hall');
  const popular = page.locator('[data-sidebar="popular"] a');
  const title = popular.locator('.game-popular-title');
  await expect(title).toContainText(game.title);
  await expect(title.locator('.lq-badge')).toHaveText('热度 138');
  await expect(popular).toContainText('累计 120 人玩过 · 近 7 天 13 人');
  await page.getByRole('button',{name:'刷新动态'}).click();
  await expect(title.locator('.lq-badge')).toHaveText('热度 148');
  await expect(popular).toContainText('累计 121 人玩过 · 近 7 天 14 人');
  await expect(popular).toHaveAttribute('href','/games/beacon-relay');
  await page.getByText('热度怎么算',{exact:true}).click();
  await expect(page.getByText(/同一账号反复打开只算一人/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await popular.locator('..').scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath('热门排行-320.png'),animations:'disabled'});
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
test('超管属性默认只读、点击编辑保存与取消、隔离预览、关联跳转及删前取消',async({page},info)=>{
  const {writes,errors}=await fixture(page,'detail','super');
  await expect(page.getByLabel('展示名称')).toHaveCount(0);
  await expect(page.locator('[data-dropzone]:visible')).toHaveCount(0);
  await expect(page.locator('[data-attributes-view]')).toContainText('点亮航标');
  await expect(page.locator('[data-game-attributes]')).toBeHidden();
  await expect(page.locator('[data-related-games] a')).toHaveAttribute('href','/manage/library/games/g-remix');
  await page.setViewportSize({width:1440,height:900});
  await page.screenshot({path:info.outputPath('教师属性-1440.png'),animations:'disabled'});
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  await expect(page.locator('[data-game-attributes]')).toBeVisible();
  await page.getByLabel('简介',{exact:true}).fill('航标接力新版');
  await page.getByRole('button',{name:'保存属性'}).click();
  await expect.poll(()=>writes.filter(x=>x.path==='/admin/games/g1'&&x.body.summary==='航标接力新版').length).toBe(1);
  await expect(page.locator('[data-game-attributes]')).toBeHidden();
  await expect(page.locator('[data-attributes-view]')).toContainText('航标接力新版');
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  await page.getByLabel('简介',{exact:true}).fill('放弃的草稿');
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.locator('[data-attributes-view]')).toContainText('航标接力新版');
  await page.getByRole('button',{name:'隔离预览'}).click();await expect(page.getByRole('link',{name:'打开隔离预览'})).toHaveAttribute('target','_blank');
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'彻底删除游戏',exact:true}).click();
  await expect(page.getByLabel('输入 beacon-relay 确认')).toBeVisible();await page.keyboard.press('Escape');
  expect(writes.filter(x=>x.path.endsWith('/deletion-plans'))).toHaveLength(1);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('教师属性-390.png'),animations:'disabled'});
  await page.locator('[data-game-versions] a').filter({hasText:'0.9.0'}).click();
  const archived=page.locator('[data-quick-actions]');
  await expect(archived.getByRole('button',{name:'隔离预览'})).toBeVisible();
  await expect(archived.getByRole('button',{name:'确认已试玩'})).toHaveCount(0);
  await expect(archived.getByRole('link',{name:'下载原包'})).toHaveAttribute('href','/api/games/v1/admin/games/g1/versions/archive/package');
  expect(errors).toEqual([]);
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
test('教师上传新版本、登记试玩、送审、管理已发布版本和删除',async({page})=>{
  const {writes,errors}=await fixture(page,'detail','teacher');
  await page.getByRole('button',{name:'上传新版本'}).click();
  const dialog=page.getByRole('dialog');
  await dialog.getByLabel('独立 HTML 游戏包（ZIP）').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:Buffer.from('synthetic')});
  await dialog.getByRole('button',{name:'上传并验收'}).click();
  await expect(page).toHaveURL(/\?version=v2$/);
  const quickbar=page.locator('[data-quick-actions]');
  await quickbar.getByRole('button',{name:'确认已试玩'}).click();await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect.poll(()=>writes.filter(x=>x.path.endsWith('/playtests')).length).toBe(1);
  await expect(quickbar.getByRole('button',{name:/上架/})).toHaveCount(0);
  await quickbar.getByRole('button',{name:'提交审核'}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.locator('[data-status-detail]')).toContainText('待超管审核');
  expect(writes.filter(x=>x.path.endsWith('/publish'))).toHaveLength(0);
  await page.locator('[data-game-versions] a').filter({hasText:'1.0.0'}).click();
  await expect(page).toHaveURL(/\?version=v1$/);
  await quickbar.getByRole('button',{name:'下架游戏'}).click();await page.getByRole('button',{name:'确认执行'}).click();
  await expect(page.locator('[data-detail-status]')).toContainText('已下架');
  await expect(quickbar.getByRole('button',{name:'提交审核'})).toBeVisible();
  await quickbar.getByRole('button',{name:'彻底删除游戏',exact:true}).click();
  await page.getByLabel('输入 beacon-relay 确认').fill('beacon-relay');await page.getByRole('button',{name:'确认彻底删除'}).click();
  await expect(page.locator('[data-game-notice]')).toHaveText('游戏已彻底删除');
  expect(errors).toEqual([]);
});
test('超管手动生成建议并核对属性草稿',async({page})=>{
  const {writes,errors}=await fixture(page,'detail','super');
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  await page.getByRole('button',{name:'拟写简介与标签'}).click();
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.getByRole('button',{name:'查看建议'}).click();
  await page.getByRole('button',{name:'采用到属性草稿'}).click();await page.keyboard.press('Escape');
  await expect(page.locator('[data-game-attributes]')).toBeVisible();
  await expect(page.getByLabel('简介',{exact:true})).toHaveValue('沿着海图点灯。');
  expect(writes.filter(x=>x.path==='/admin/games/g1')).toHaveLength(0);
  await page.getByRole('button',{name:'保存属性'}).click();await expect.poll(()=>writes.filter(x=>x.path==='/admin/games/g1').length).toBe(1);
  expect(errors).toEqual([]);
});

test('执行器等待状态在刷新后可见且候选版本无法提前发布',async({page})=>{
  const {errors}=await fixture(page,'detail','teacher',true);
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'上传新版本'}).click();
  const dialog=page.getByRole('dialog');
  await dialog.getByLabel('独立 HTML 游戏包（ZIP）').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:Buffer.from('synthetic')});
  await dialog.getByRole('button',{name:'上传并验收'}).click();
  await expect(page.locator('[data-status-detail]')).toContainText('待浏览器验收');
  await page.reload();
  await expect(page.locator('[data-status-detail]')).toContainText('静态检查已通过，启用验收后自动继续');
  const quickbar=page.locator('[data-quick-actions]');
  await expect(quickbar.getByRole('button',{name:'验收报告'})).toBeVisible();
  await expect(quickbar.getByRole('button',{name:/上架/})).toHaveCount(0);
  await expect(quickbar.getByRole('button',{name:'隔离预览'})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
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

test('学生工作区按版本切换展示署名与审核，可管理许可、下架、提交审核及删除',async({page},info)=>{
  const {writes,errors}=await fixture(page,'detail','student');
  await page.setViewportSize({width:320,height:844});
  const versions=page.locator('[data-game-versions] li');
  await expect(versions.first()).toContainText('待超管审核');
  const v1Row=versions.filter({hasText:'1.0.0'});
  await expect(v1Row).toContainText('内容审核通过');
  await expect(v1Row).toContainText('改编自 原作者');
  await expect(v1Row).toContainText('#原作标签');
  const quickbar=page.locator('[data-quick-actions]');
  await expect(quickbar.getByRole('button',{name:/上架/})).toHaveCount(0);
  await expect(page.getByLabel('教师精选')).toHaveCount(0);
  // Jump to candidate 1.1.0 and verify status switches, then jump back to 1.0.0.
  await page.locator('[data-game-versions] a').filter({hasText:'1.1.0'}).click();
  await expect(page).toHaveURL(/\?version=candidate$/);
  await expect(page.locator('[data-status-detail]')).toContainText('等待人工确认');
  await page.locator('[data-game-versions] a').filter({hasText:'1.0.0'}).click();
  await expect(page).toHaveURL(/\?version=v1$/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await page.screenshot({path:info.outputPath('学生版本管理-320.png'),animations:'disabled'});
  await quickbar.getByRole('button',{name:'关闭二开许可'}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(quickbar.getByRole('button',{name:'允许二次开发'})).toBeVisible();
  await expect(quickbar.getByRole('link',{name:'下载原包'})).toHaveAttribute('href','/api/games/v1/admin/games/g1/versions/v1/package');
  await quickbar.getByRole('button',{name:'下架游戏'}).click();
  await page.getByRole('button',{name:'确认执行',exact:true}).click();
  await expect(quickbar.getByRole('button',{name:'提交审核'})).toBeVisible();
  await quickbar.getByRole('button',{name:'停用此版本'}).click();
  await page.getByRole('button',{name:'确认执行',exact:true}).click();
  await expect(quickbar.getByRole('button',{name:'停用此版本'})).toHaveCount(0);
  await quickbar.getByRole('button',{name:'提交审核'}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.locator('[data-status-detail]')).toContainText('待超管审核');
  await quickbar.getByRole('button',{name:'删除此版本'}).click();
  await page.getByRole('button',{name:'确认执行',exact:true}).click();
  await expect(versions.filter({hasText:'1.0.0'})).toHaveCount(0);
  expect(writes.filter(x=>x.path.includes('/versions/v1'))).toHaveLength(4);
  expect(errors).toEqual([]);
});

test('超管在小游戏审核台树形浏览解压包、查看AI报告、触发AI审核并提交决定',async({page},info)=>{
  const {writes,errors}=await fixture(page,'reviews','teacher');
  await page.setViewportSize({width:1440,height:900});
  // Sidebar under 资源库 > 小游戏 shows both 小游戏管理 and 小游戏审核 with 小游戏审核 active.
  const gameGroup=page.locator('[data-nav-group="library:小游戏"]');
  await expect(gameGroup.getByRole('link',{name:'小游戏管理'})).toBeVisible();
  await expect(gameGroup.getByRole('link',{name:'小游戏审核'})).toHaveAttribute('aria-current','page');
  // Full-width review workbench renders queue, AI report, and embedded Material Explorer tree.
  await expect(page.locator('[data-review-list]')).toContainText('航标接力');
  await expect(page.locator('[data-review-report]')).toContainText('含少量轻度浪漫对白');
  const explorer=page.locator('[data-review-explorer]');
  await expect(explorer.getByRole('tree')).toContainText('main.html');
  await page.screenshot({path:info.outputPath('超管审核-1440-概览.png'),animations:'disabled'});
  // Clicking an AI-flagged file in the report jumps straight into the Material Explorer code preview.
  await page.locator('.game-review-evidence__jump').filter({hasText:'js/game.js'}).click();
  await expect(explorer).toContainText('export function startGame()');
  await expect(page.getByRole('link',{name:'下载完整文件包'})).toHaveAttribute('href','/api/games/v1/moderation/reviews/v1/package');
  await page.screenshot({path:info.outputPath('超管审核-1440.png'),animations:'disabled'});
  // Toggle automatic AI review setting.
  await page.getByRole('switch',{name:'新提交自动 AI 审核'}).click();
  await expect.poll(()=>writes.filter(x=>x.path==='/moderation/reviews/settings'&&x.body.autoAiReview===true).length).toBe(1);
  // Trigger on-demand AI review, then verify super admin can still decide cleanly.
  await page.getByRole('button',{name:'重新 AI 查验'}).click();
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect.poll(()=>writes.filter(x=>x.path.endsWith('/ai-review')).length).toBe(1);
  // Responsive check on narrow viewport.
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('超管审核-390.png'),animations:'disabled'});
  await page.getByRole('button',{name:'退回作者',exact:true}).click();
  await page.getByLabel('退回理由（将反馈给作者）').fill('请替换此段文本');
  await page.getByRole('button',{name:'确认退回作者'}).click();
  await expect.poll(()=>writes.filter(x=>x.path.endsWith('/decision')).length).toBe(1);
  expect(writes.find(x=>x.path.endsWith('/decision'))?.body).toEqual({decision:'reject',reason:'请替换此段文本',expectedDigest:'a'.repeat(64)});
  await expect(page.locator('[data-game-notice]')).toContainText('已退回作者');
  await expect(page.locator('[data-review-report]')).toContainText('请替换此段文本');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(errors).toEqual([]);
});


test('教师从大厅设置逐游戏放行，冲突保留选择，倒计时在本地结束', async ({page},info) => {
  const {errors} = await fixture(page,'hall','teacher');
  const games = [{id:'a', title:'星图'}, {id:'b', title:'课间拼图'}];
  let stored = {mode:'auto', gameIds:[] as string[], durationMinutes:0, revision:3, expiresAt:0};
  let writes:any[] = [];
  const pack = () => ({...stored, offeringId:8, scheduledNow:true, serverNow:Date.now(),
    games:games.filter(g=>stored.gameIds.includes(g.id)), csrfToken:'synthetic-only'});
  await page.route('**/classroom-access', r=>r.fulfill({json:{classrooms:[{id:8,name:'网络工程 · 合成班'}]}}));
  await page.route('**/classroom-access/8', r=>{
    if(r.request().method()==='PUT'){
      const body = r.request().postDataJSON();
      writes.push(body);
      if(writes.length===1) {
        stored.revision++;
        return r.fulfill({status:409,json:{detail:'版本冲突'}});
      }
      stored={...body,revision:stored.revision+1,expiresAt:Date.now()+3400};
    }
    return r.fulfill({json:pack()});
  });
  await page.route('**/catalog?**', r=>{
    const u=new URL(r.request().url());
    expect(u.searchParams.get('genres')).toBe('');
    expect(u.searchParams.get('tags')).toBe('');
    return r.fulfill({json:{items:games.filter(g=>g.title.includes(u.searchParams.get('q')||''))}});
  });
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'游玩设置'}).click();
  await page.getByRole('button',{name:'课堂小游戏设置'}).click();
  const editor=page.locator('[data-policy-form]');
  await expect(editor.getByLabel('跟随课表')).toBeChecked();
  const spacing = await editor.evaluate(form=>{
    const legend=form.querySelector('legend')!.getBoundingClientRect();
    const controls=form.querySelector('.policy-choices')!.getBoundingClientRect();
    return controls.top-legend.bottom;
  });
  expect(spacing).toBeLessThanOrEqual(20);

  await editor.getByLabel('临时放行').check();
  await expect(editor.getByLabel('应用到全部游戏')).toBeChecked();
  await editor.getByLabel('应用到全部游戏').uncheck();
  await editor.getByLabel('持续时间（分钟）').fill('13');
  await editor.getByLabel('搜索指定游戏').fill('星');
  await editor.getByLabel('星图',{exact:true}).check();
  await editor.getByLabel('搜索指定游戏').fill('拼');
  await editor.getByLabel('课间拼图',{exact:true}).check();
  await expect(editor.locator('[data-policy-selected]')).toContainText('已选 2 款');
  await editor.getByRole('button',{name:'应用规则'}).click();
  await expect(editor.locator('[data-policy-feedback]')).toContainText('你的选择已保留');
  await editor.getByRole('button',{name:'应用规则'}).click();
  expect(writes.map(w=>w.expectedRevision)).toEqual([3,4]);
  expect(writes[1]).toMatchObject({mode:'allow',durationMinutes:13,gameIds:['a','b']});
  await expect(editor.locator('[data-policy-status]')).toContainText('已临时放行');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('课堂小游戏放行-390.png'), animations:'disabled'});
  await expect(editor.locator('[data-policy-status]')).toContainText('当前跟随课表',{timeout:6500});
  expect(writes).toHaveLength(2);
  await editor.getByLabel('临时禁止').check();
  await editor.getByLabel('应用到全部游戏').check();
  await editor.getByRole('button',{name:'应用规则'}).click();
  await expect(editor.locator('[data-policy-status]')).toContainText('已临时禁止');
  expect(writes[2]).toMatchObject({mode:'deny',gameIds:[]});
  await page.setViewportSize({width:1440,height:940});
  await page.screenshot({path:info.outputPath('课堂小游戏设置-桌面.png'), animations:'disabled'});
  await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
  await page.setViewportSize({width:320,height:760});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await page.screenshot({path:info.outputPath('课堂小游戏设置-320-深色.png'), animations:'disabled'});

  await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'课堂小游戏设置'})).toBeFocused();
  expect(errors).toEqual([]);
});


test('课堂边界复核关闭已载入的游戏，续租仅上报可见游戏画面', async ({page}) => {
  await page.clock.install();
  const originTime = Date.now();
  const samples:any[] = [];
  const {errors} = await fixture(page,'host');
  await page.route('**/games/g1/launches', r=>r.fulfill({json:{
    leaseId:'sample-lease',serverNow:originTime,expiresAt:originTime+90000,accessUntil:originTime+14000,
    contentEntryUrl:content+'/main.html',definition,versionId:'v1',
  }}));
  await page.route('**/launches/sample-lease/renew', r=>{
    samples.push(r.request().postDataJSON());
    return r.fulfill({status:403,json:{detail:{code:'CLASSROOM_GAME_RESTRICTED',message:'课表显示现在是上课时间，课间可再来。'}}});
  });
  await page.reload();
  await expect(page.frameLocator('.game-frame-box iframe').locator('#result')).toHaveText('合成玩家');
  const visible = async (value:boolean) => page.evaluate(value=>{
    Object.defineProperty(document,'hidden',{value:!value,configurable:true});
    document.dispatchEvent(new Event('visibilitychange'));
  },value);
  await page.clock.fastForward(1500);
  await visible(false);
  await page.clock.fastForward(3500);
  await visible(true);
  await page.clock.fastForward(12000);
  await expect(page.locator('[data-game-notice]')).toContainText('课间可再来');
  await expect(page.locator('.game-frame-box iframe')).toHaveCount(0);
  expect(samples).toHaveLength(1);
  expect(samples[0].usageAnchor).toBe(originTime+90000);
  const intervals=samples[0].playIntervals;
  expect(intervals).toHaveLength(2);
  expect(intervals[1][0]-intervals[0][1]).toBeGreaterThanOrEqual(3000);
  expect(errors).toEqual([]);
});


test('课堂页直达本课堂的规则，读取签名后保存并归还焦点', async ({page}) => {
  const {errors} = await fixture(page,'classroom','teacher');
  let token = '', saved = false;
  await page.route('**/classroom-access/123', r => {
    const put = r.request().method() === 'PUT';
    if (put) {
      const request=r.request();
      token=request.headers()['x-langame-csrf'];
      expect(request.postDataJSON()).toMatchObject({expectedRevision:0,mode:'deny',gameIds:[]});
      saved=true;
    }
    return r.fulfill({json:{offeringId:123,revision:put?1:0, mode:put?'deny':'auto',
      games:[],gameIds:[],durationMinutes:put?15:0,scheduledNow:false,expiresAt:put?Date.now()+900000:null,
      serverNow:Date.now(),csrfToken:'classroom-only-token'}});
  });
  const entry=page.getByRole('button',{name:'小游戏设置',exact:true});
  await expect(entry).toHaveAttribute('data-game-offering','123');
  await entry.click();
  const editor=page.locator('[data-policy-form]');
  await expect(editor).toContainText('网络工程 · 合成班');
  await expect(editor.getByRole('combobox')).toHaveCount(0);
  await editor.getByLabel('临时禁止').check();
  await editor.getByRole('button',{name:'应用规则'}).click();
  await expect(editor.locator('[data-policy-status]')).toContainText('已临时禁止');
  expect(saved).toBe(true);
  expect(token).toBe('classroom-only-token');
  await page.keyboard.press('Escape');
  await expect(entry).toBeFocused();
  expect(errors).toEqual([]);
});
