import {EditorStore,mergeCanonical} from './state.js';
import {clone,equal,uid,locate,rootSelection,removeBlocks,insertionList,freshInstance,walkBlocks,setAt,at} from './model.js';
import {createApi,jsonRequest,ApiError} from './api.js';
import {Drafts} from './drafts.js';
import {EditorBridge} from './bridge.js';
import {CanvasController} from './canvas_controller.js';
import {movedSelection,offsetPastedElements} from './geometry.js';
import {PropsPanel} from './props_panel.js';
import {REGISTRY,makeBlock,LAYOUTS} from './registry.js';
import {renderPageRail,pageCommand} from './page_rail.js';
import {convertLayout} from './layout_conversion.js';
import {groupSelection,ungroupSelection} from './grouping.js';
import {openActions} from './actions_builder.js';
import {openHomeEditor} from './home_editor.js';
import {openBackground} from './background_editor.js';
import {openAiPanel} from './ai_panel.js';
import {openHtmlEditor} from './html_editor.js';
import {openMedia,openTemplates} from './media_picker.js';
import {el,button,field,dialog,downloadJson,reportError,choiceSample} from './ui.js';
import {configureColorPicker} from '../ui_color_picker.js';

const config=JSON.parse(document.getElementById('lessondoc-editor-config').textContent);
const $=id=>document.getElementById('lde-'+id),api=createApi(config.packId,config.lessonNo);
let store,bridge,props,drafts,saveTimer,channel,clipboard=null,disposed=false;
const warn=message=>{$('warning').textContent=message||'';$('warning').hidden=!message;};
function error(error) {reportError(error,$('error'));if(store){$('error').append(button('下载本地草稿',()=>downloadJson(store.model)));if(store.blocked?.kind==='conflict')$('error').append(button('查看并处理冲突',reviewConflict));}}
function guard(fn) {return async(...args)=>{try{return await fn(...args);}catch(e){error(e);}};}

function renderStatus() {
    if(!store)return;
    const state=store.pending?'saving':store.blocked||store.retry?'error':store.dirty?'dirty':'saved';
    $('save-state').dataset.state=state;$('save-state').textContent={saving:'正在保存…',error:store.blocked?.kind==='conflict'?'版本冲突':store.retry?'网络中断，待重试':'内容待检查',dirty:'有未保存修改',saved:'已保存'}[state];
    $('save').disabled=!!store.pending;$('undo').disabled=!store.undoStack.length||store.ui.trial;$('redo').disabled=!store.redoStack.length||store.ui.trial;
    $('title').textContent=store.model.kind==='home'?store.model.course.name:store.model.title||'学习文档';
    $('context').textContent=store.ui.trial?'预览当前修改 · 尚未保存的内容也包含在内':store.model.kind==='home'?'课程首页 · 流式编辑':'第 '+(store.ui.slide+1)+' / '+store.model.slides.length+' 页 · '+(LAYOUTS[store.model.slides[store.ui.slide]?.layout]||'正文');
}
function scheduleSave() {clearTimeout(saveTimer);if(!store.ui.trial&&!store.blocked&&!store.retry)saveTimer=setTimeout(()=>save(false),4500);}
async function save(manual=true) {
    if(!store)return;
    if(store.blocked){if(manual&&store.blocked.kind==='conflict')reviewConflict();else if(manual)error(store.blocked.error);return;}
    const attempt=store.beginSave();if(!attempt)return;
    try{const result=attempt.restoreId?await api.restore(attempt.restoreId,attempt.revision,attempt.operation_id):await api.save(attempt);store.savedResponse(result);$('error').hidden=true;warn((result.warnings||[]).join(' · '));drafts?.write(store);channel?.postMessage({lessonNo:config.lessonNo,revision:store.revision});if(store.dirty)scheduleSave();}
    catch(e){if(store.pending)store.saveFailed(e);error(e);}
}
async function reviewConflict() {
    try{const remote=await api.load();dialog('检测到其他修改',({body,foot,close})=>{
        body.append(el('p','lde-muted','服务器版本已经更新。请比较正文后选择保留哪份内容。保留本地修改会以刚刚读取的服务器版本为基线再次校验。'));
        const code=el('textarea','lde-code');code.readOnly=true;code.value=JSON.stringify(remote.document,null,2);body.append(code);
        foot.append(button('下载本地草稿',()=>downloadJson(store.model)),button('载入服务器版本',()=>{store.adoptServer(remote);$('error').hidden=true;close();}),button('保留本地修改继续编辑',()=>{store.adoptServer(remote,{keepLocal:true});$('error').hidden=true;warn('已保留本地修改，请检查后保存。');close();},'lde-button lde-primary'));
    },{wide:true});}catch(e){error(e);}
}
function renderLayers() {
    const root=$('layers');root.replaceChildren();const home=store.model.kind==='home',scope=home?store.model:store.model.slides[store.ui.slide];
    const append=(b,path,ancestors)=>{const selected=store.ui.selection.includes(b.id),name=b.name||b.md||b.label||b.title||REGISTRY[b.type]?.label||b.type;
        const row=button((b.hidden?'◌ ':'')+(name||'元素').slice(0,32),event=>{if(event.shiftKey)store.select(selected?store.ui.selection.filter(x=>x!==b.id):[...store.ui.selection,b.id]);else store.select([b.id]);},'lde-layer'+(selected?' is-selected':''));row.style.paddingLeft=(8+ancestors.length*12)+'px';row.setAttribute('aria-pressed',String(selected));root.append(row);};
    walkBlocks(scope,append);if(!home&&store.model.globals?.length){root.append(el('small','lde-muted','全局元素'));walkBlocks({globals:store.model.globals},append);}
    if(!root.children.length)root.append(el('p','lde-muted','从上方选择一个元素开始。'));
}
function insert(block,{point,targetId}={}) {
    const index=store.ui.slide,home=store.model.kind==='home',slide=store.model.slides?.[index];
    if(home&&block.type==='group')throw new Error('首页使用流式内容。请在课次页面插入此组合，或复制其中的文字、图片等内容。');
    store.command('插入'+(REGISTRY[block.type]?.label||'元素'),model=>{
        const scope=!home&&(['title','section'].includes(slide.layout)||block.type==='group')?'overlay':'page';
        const positioned=!home&&(slide.layout==='canvas'||scope==='overlay');
        const instance=clone(block);if(positioned&&!instance.frame)instance.frame={x:100,y:120,w:600,h:260};
        if(home)delete instance.frame;
        if(positioned&&point){instance.frame.x=point.x-instance.frame.w/2;instance.frame.y=point.y-instance.frame.h/2;offsetPastedElements([instance]);}
        const target=targetId&&locate(model,targetId),targetList=target&&!target.block.frame?at(model,target.path.slice(0,-1)):null;
        if(!positioned&&Array.isArray(targetList))targetList.splice(target.path.at(-1)+1,0,instance);
        else insertionList(model,index,home?(store.ui.homeTarget||'page'):scope).push(instance);
    });store.select([block.id]);$('stage').focus({preventScroll:true});
}
function addType(type,options={}) {if(type==='media'){const page=store.ui.slide;openMedia(config,resource=>{if(page!==store.ui.slide){error(new Error('当前页已变化，请重新插入素材。'));return;}insert(makeBlock('media',{resource}),options);},error);return;}insert(makeBlock(type,{positioned:store.model.slides?.[store.ui.slide]?.layout==='canvas'}),options);}
function renderPalette() {const q=$('search').value.trim().toLowerCase(),root=$('palette');root.replaceChildren();for(const entry of Object.values(REGISTRY)){if(q&&!(entry.label+entry.category+entry.type).toLowerCase().includes(q))continue;if(store.model.kind==='home'&&entry.type==='group')continue;const b=button('',guard(()=>addType(entry.type)),'lde-element');b.draggable=true;b.addEventListener('dragstart',event=>{event.dataTransfer.setData('application/x-lessondoc-block',entry.type);event.dataTransfer.effectAllowed='copy';});b.append(el('small','',entry.category),el('strong','',entry.label));root.append(b);}}
function remove() {if(!store.ui.selection.length)return;let pruned=0;store.command('删除元素',model=>{pruned=removeBlocks(model,store.ui.selection);});store.select([]);if(pruned)warn('已同步移除 '+pruned+' 个指向已删除元素的动作。可撤销恢复。');}
async function copy(cut=false) {
    const selected=rootSelection(store.model,store.ui.selection);if(!selected.length)return;
    const payload={format:'lessondoc-elements/1',packId:config.packId,lessonNo:config.lessonNo,elements:selected.map(x=>clone(x.block))};
    const text=JSON.stringify(payload);clipboard=payload;
    let external=false;
    try{await navigator.clipboard.writeText(text);external=true;warn('已复制，可粘贴到其他学习文档。');}catch{warn(cut?'浏览器未授权系统剪贴板，已保留原元素。可在本页复制粘贴。':'已复制到本页剪贴板；浏览器未授权跨页剪贴板。');}
    if(cut&&external)remove();
}
async function paste() {
    let value=clipboard;
    try{const raw=await navigator.clipboard.readText();if(raw.length<=2*1024*1024){const parsed=JSON.parse(raw);if(parsed.format==='lessondoc-elements/1')value=parsed;}}catch{/* internal copy remains usable on HTTP origins */}
    if(!value||value.format!=='lessondoc-elements/1'||!Array.isArray(value.elements))throw new Error('剪贴板中没有可粘贴的学习文档元素。');
    const before=clone(store.model),slideIndex=store.ui.slide;
    let elements=freshInstance(value.elements);
    if(value.packId!==config.packId||value.lessonNo!==config.lessonNo){const result=await jsonRequest(api.base+'/copy-element'+api.query,'POST',{source_pack_id:value.packId,source_lesson_no:value.lessonNo,elements:value.elements});elements=result.elements;warn(result.warnings.join(' · '));}
    if(!equal(before,store.model)||slideIndex!==store.ui.slide)throw new Error('粘贴准备期间正文或当前页已变化，请重新粘贴。');
    const candidate=clone(before),home=candidate.kind==='home',page=candidate.slides?.[slideIndex];
    for(const [index,block] of elements.entries()){
        if(home&&block.type==='group')throw new Error('首页使用流式内容，请复制组合中的具体元素。');
        if(home||(!value.elements[index].frame&&page?.layout!=='canvas'&&!['title','section'].includes(page?.layout)&&block.type!=='group'))delete block.frame;
        insertionList(candidate,store.ui.slide,home?(store.ui.homeTarget||'page'):block.frame?'overlay':'page').push(block);
    }
    offsetPastedElements(elements);
    const checked=await api.validate(candidate);if(!checked.valid)throw new Error(checked.warnings.join('\n'));
    if(!equal(before,store.model)||slideIndex!==store.ui.slide)throw new Error('粘贴预检期间正文或当前页已变化，请重新粘贴。');
    store.command('粘贴元素',model=>{Object.keys(model).forEach(k=>delete model[k]);Object.assign(model,checked.document);});store.select(elements.map(b=>b.id));
}
function source(mode='document') {
    const selected=locate(store.model,store.ui.selection[0]),index=store.ui.slide,readonly=mode==='document',before=clone(store.model),serial=store.serial,path=mode==='selection'?selected?.path:['slides',index];if(mode==='selection'&&!selected)return;
    const value=readonly?before:mode==='selection'?selected.block:before.slides[index];
    dialog(readonly?'文档 JSON（只读）':mode==='selection'?'选中元素 JSON':'当前页 JSON',({body,foot,close})=>{
        body.append(el('p','lde-muted',readonly?'下载后可作为本地恢复稿保存。':'应用前会执行与保存相同的结构和安全校验；预检失败不会修改正文。'));
        const input=el('textarea','lde-code');input.value=JSON.stringify(value,null,2);input.readOnly=readonly;input.spellcheck=false;const message=el('div','lde-dialog-error');body.append(input,message);foot.append(button('下载完整草稿',()=>downloadJson(store.model)),button('关闭',close));
        if(!readonly)foot.append(button('预检并应用',async()=>{try{
            if(serial!==store.serial)throw new Error('打开面板后正文已变化，请关闭后重新打开。');
            const sourceText=input.value,parsed=JSON.parse(sourceText),candidate=clone(before);setAt(candidate,path,parsed);const result=await api.validate(mergeCanonical(before,store.model,candidate));
            if(!result.valid)throw new Error(result.warnings.join('\n')||'内容不符合模型要求');
            if(serial!==store.serial||sourceText!==input.value)throw new Error('预检期间内容已变化，请再次预检。');
            const document=mergeCanonical(before,store.model,result.document);
            store.command('应用 JSON',model=>{Object.keys(model).forEach(k=>delete model[k]);Object.assign(model,document);});store.select(mode==='selection'&&parsed.id?[parsed.id]:[]);warn(result.warnings.join(' · '));close();
        }catch(e){message.textContent=e.message;}},'lde-button lde-primary'));
    },{wide:true});
}
function layout() {
    if(store.model.kind==='home')return;
    const index=store.ui.slide;dialog('页面版式',({body,foot,close})=>{
        body.append(el('p','lde-muted','转换保留现有元素。自由画布转流式后按从上到下排序；可用撤销恢复原位置。'));
        const grid=el('div','lde-palette');body.append(grid);
        for(const [kind,label]of Object.entries(LAYOUTS)){const item=button('',guard(async()=>{
            const serial=store.serial,candidate=clone(store.model),converted=convertLayout(candidate.slides[index],kind,bridge.api.measureFlowFrames(index));candidate.slides[index]=converted;
            const check=await api.validate(candidate);if(!check.valid)throw new Error(check.warnings.join('\n'));
            if(serial!==store.serial||index!==store.ui.slide)throw new Error('预检期间正文或当前页已变化，请重新转换。');
            store.command('转换版式',model=>model.slides[index]=check.document.slides[index]);store.select([]);close();
        }),'lde-button lde-layout-choice');item.append(choiceSample('layout',kind),el('span','',label));item.setAttribute('aria-label',label);grid.append(item);}
        foot.append(button('取消',close));
    });
}
async function history() {
    const entries=await api.history();dialog('最近 20 个历史版本',({body,foot,close})=>{
        body.append(el('p','lde-muted','历史保存正文。已经从材料库删除的素材可能无法恢复。'));
        if(!entries.length)body.append(el('p','lde-muted','保存产生正文变化后，原版本会出现在这里。'));
        for(const entry of entries){const row=el('div','lde-list-row');row.append(el('strong','',entry.created_at),el('small','',entry.source),button('查看',guard(async()=>{
            const snapshot=await api.revision(entry.id);dialog('历史版本 '+entry.created_at,({body:b,foot:f,close:c})=>{
                const text=el('textarea','lde-code');text.readOnly=true;text.value=JSON.stringify(snapshot.document,null,2);b.append(text);
                for(const d of snapshot.diagnostics||[])b.append(el('p','lde-dialog-error',d.message));
                f.append(button('关闭',c));if(snapshot.document)f.append(button('恢复并保存',guard(async()=>{
                    if(store.pending)throw new Error('请等待正在进行的保存结束');
                    const serial=store.serial,check=await api.validate(snapshot.document);if(!check.valid)throw new Error(check.warnings.join('\n'));
                    if(serial!==store.serial||store.pending)throw new Error('预检期间正文或保存状态已变化，请重新恢复。');
                    store.command('恢复历史',model=>{Object.keys(model).forEach(k=>delete model[k]);Object.assign(model,check.document);});
                    const attempt=store.beginSave({restoreId:entry.id});if(!attempt)throw new Error('请先处理当前保存冲突');
                    try{const result=await api.restore(entry.id,attempt.revision,attempt.operation_id);store.savedResponse(result);warn(result.warnings.join(' · '));channel?.postMessage({lessonNo:config.lessonNo,revision:store.revision});c();close();}catch(e){store.saveFailed(e);throw e;}
                }),'lde-button lde-primary'));
            },{wide:true});
        })));body.append(row);}foot.append(button('关闭',close));
    });
}
function template() {
    const item=locate(store.model,store.ui.selection[0]);if(!item)return;
    dialog('保存为我的元素',({body,foot,close})=>{let name=item.block.name||REGISTRY[item.block.type]?.label||'自定义元素';body.append(field('元素名称',name,v=>name=v),el('p','lde-muted','内部交互与素材会一并保存。对其他页面和外部元素的动作会移除。'));foot.append(button('保存',guard(async()=>{const result=await jsonRequest('/api/lessondoc/editor/custom-elements','POST',{pack_id:config.packId,lesson_no:config.lessonNo,name,element:item.block});warn((result.warnings||[]).join(' · ')||'已保存到“我的元素”。');close();}),'lde-button lde-primary'));});
}
function resizeStage() {
    const value=$('zoom').value,wrap=$('frame-wrap');
    const fit=$('zoom').dataset.fit==='true'||store?.model.kind==='home';$('zoom-value').value=fit?'适应':Math.round(Number(value)*100)+'%';$('zoom-fit').setAttribute('aria-pressed',String(fit));$('zoom').setAttribute('aria-valuetext',$('zoom-value').value);$('zoom').disabled=store?.model.kind==='home';
    if(fit){wrap.style.width='100%';wrap.style.height='100%';}
    else{wrap.style.width=Math.round(1280*Number(value)/.97)+'px';wrap.style.height=Math.round(720*Number(value)/.97)+'px';}
}
function isTyping(event) {return event.isComposing||event.target.closest?.('input,textarea,select,[contenteditable="true"]');}
function shortcuts(event) {
    if(!store)return;const mod=event.ctrlKey||event.metaKey,key=event.key.toLowerCase();
    if(mod&&key==='s'){event.preventDefault();save();return;}
    if(event.target.closest?.('.ls-popover'))return;
    if(isTyping(event))return;
    if(event.key==='Escape'){if(store.ui.trial){toggleTrial();return;}if(store.ui.groupPath.length){store.ui.groupPath.pop();store.select([]);}else store.select([]);return;}
    if(store.ui.trial)return;
    if(mod&&key==='z'){event.preventDefault();event.shiftKey?store.redo():store.undo();}
    else if(mod&&key==='y'){event.preventDefault();store.redo();}
    else if(mod&&key==='c'){event.preventDefault();guard(copy)();}
    else if(mod&&key==='x'){event.preventDefault();guard(()=>copy(true))();}
    else if(mod&&key==='v'){event.preventDefault();guard(paste)();}
    else if(['Delete','Backspace'].includes(event.key)){event.preventDefault();guard(remove)();}
    else if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)&&store.ui.selection.length){event.preventDefault();const step=event.shiftKey?10:1,delta={x:event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0,y:event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0};store.command('移动元素',model=>{for(const[id,frame]of movedSelection(rootSelection(model,store.ui.selection),delta))locate(model,id).block.frame=frame;},{coalesce:'nudge'});}
}
function toggleTrial() {bridge.trial(!store.ui.trial);$('preview').textContent=store.ui.trial?'返回编辑':'预览当前修改';document.getElementById('lessondoc-editor').classList.toggle('is-trial',store.ui.trial);renderStatus();}
async function inspectRemote() {
    if(!store||store.pending||disposed)return;try{const result=await api.load();if(result.revision===store.revision)return;if(store.dirty){store.blocked={kind:'conflict',error:new ApiError('另一个页面已更新此文档，请处理版本冲突。',409)};error(store.blocked.error);renderStatus();}else store.adoptServer(result);}catch(e){error(e);}
}

async function boot() {
    const loaded=await api.load();store=new EditorStore(loaded.document,loaded.revision);
    const target=config.slideId?store.model.slides?.findIndex(s=>s.id===config.slideId):-1;store.ui.slide=Math.max(0,Math.min((store.model.slides?.length||1)-1,target>=0?target:config.slide-1));
    try{drafts=new Drafts(config.userId,config.packId,config.lessonNo);}catch{warn('浏览器无法存储本地草稿，请及时保存或下载恢复稿。');}
    $('document').value=String(config.lessonNo);
    const ai=guard(async(mode)=>{await save();if(store.dirty)throw new Error('请先保存当前修改后再使用 AI。');openAiPanel(store,api,mode,error);});
    props=new PropsPanel($('props'),store,{copy:guard(copy),remove:guard(remove),deletePage:guard(()=>pageCommand(store,'delete')),source,layout,template,ai,html:guard(convert=>openHtmlEditor(store,api,config,bridge,{convert})),background:()=>openBackground(store,api,config,error),
        group:guard(()=>{let id;store.command('组合元素',model=>{id=groupSelection(model,store.ui.selection);});store.select([id]);}),
        ungroup:guard(()=>{let ids;store.command('拆开组合',model=>{ids=ungroupSelection(model,store.ui.selection[0]);});store.select(ids);}),
        actions:()=>openActions(store,api,error),home:()=>openHomeEditor(store,api,error,config),error,media:id=>openMedia(config,resource=>store.command('更换素材',model=>{const b=locate(model,id).block;Object.assign(b,{src:resource.src,kind:resource.kind});}),error)});
    bridge=new EditorBridge($('frame'),store,error);await bridge.connect();new CanvasController(bridge,store,error,guard(addType));
    configureColorPicker({userId:config.userId,resolveColor:color=>color?.startsWith('#')?color:bridge.window.getComputedStyle(bridge.document.documentElement).getPropertyValue('--'+color).trim()});
    store.subscribe(event=>{
        // Deletions and server normalization may invalidate selected objects or page indexes.
        store.ui.slide=Math.max(0,Math.min(store.ui.slide,(store.model.slides?.length||1)-1));
        store.ui.selection=store.ui.selection.filter(id=>locate(store.model,id));
        renderStatus();props.render();renderLayers();
        if(['document','saved','page'].includes(event.type)){renderPageRail($('pages'),store,error);renderHomeTarget();}
        if(event.type==='document'){drafts?.schedule(store,()=>warn('本地草稿空间不足，请下载恢复稿并及时保存。'));scheduleSave();}
        if(event.type==='page'){const url=new URL(location.href);const slide=store.model.slides?.[store.ui.slide];if(slide){url.searchParams.set('slide_id',slide.id);historyReplace(url);}props.render(true);}
    });
    renderStatus();renderPalette();renderLayers();renderPageRail($('pages'),store,error);renderHomeTarget();props.render(true);resizeStage();
    $('zoom').addEventListener('input',()=>{$('zoom').dataset.fit='false';resizeStage();});$('zoom-fit').addEventListener('click',()=>{$('zoom').dataset.fit='true';resizeStage();});
    if(config.lessonNo)for(const[op,label]of[['previous','前移'],['next','后移'],['duplicate','复制页'],['delete','删页']])$('page-actions').append(button(label,guard(()=>pageCommand(store,op))));
    $('search').addEventListener('input',renderPalette);$('save').addEventListener('click',()=>save());$('undo').addEventListener('click',()=>store.undo());$('redo').addEventListener('click',()=>store.redo());$('history').addEventListener('click',guard(history));$('preview').addEventListener('click',toggleTrial);$('source').addEventListener('click',()=>source());$('templates').addEventListener('click',()=>openTemplates(config,insert,error));$('zoom').addEventListener('change',resizeStage);
    $('document').addEventListener('change',()=>{const value=$('document').value;location.href='/materials/lessondoc-editor/'+config.packId+'?lesson='+value+'&return_to='+encodeURIComponent(config.returnUrl);$('document').value=String(config.lessonNo);});
    $('toggle-elements').addEventListener('click',()=>{$('elements').classList.toggle('is-open');$('props').classList.remove('is-open');});$('toggle-props').addEventListener('click',()=>{$('props').classList.toggle('is-open');$('elements').classList.remove('is-open');});
    document.addEventListener('keydown',shortcuts);bridge.api.on('keydown',shortcuts);
    window.addEventListener('beforeunload',event=>{try{drafts?.write(store);}catch{/* visible warning already covers draft quota */}if(store.dirty||store.pending){event.preventDefault();event.returnValue='';}});
    window.addEventListener('pageshow',inspectRemote);
    try{channel=new BroadcastChannel('lanshare-lessondoc-'+config.userId+'-'+config.packId);channel.onmessage=event=>{if(event.data.lessonNo===config.lessonNo||config.lessonNo===0)inspectRemote();};}catch{/* pageshow and conditional saves still detect stale versions */}
    window.addEventListener('pagehide',event=>{if(event.persisted)return;disposed=true;clearTimeout(saveTimer);drafts?.destroy();channel?.close();bridge.destroy();});
    const draft=drafts?.candidate(loaded);if(draft)dialog(draft.conflict?'发现另一版本上的本地草稿':'恢复本地草稿',({body,foot,close})=>{
        body.append(el('p','lde-muted','保存时间：'+new Date(draft.updatedAt).toLocaleString()+(draft.conflict?'。服务器版本已经变化，请下载对照，或明确载入草稿后检查。':'。本地草稿尚未保存到服务器。')));
        foot.append(button('下载草稿',()=>downloadJson(draft.document)),button('使用服务器版本',()=>{drafts.remove();close();}),button('载入草稿供检查',guard(async()=>{const checked=await api.validate(draft.document);if(!checked.valid)throw new Error(checked.warnings.join('\n'));store.command('载入本地草稿',model=>{Object.keys(model).forEach(k=>delete model[k]);Object.assign(model,checked.document);});clearTimeout(saveTimer);warn('已载入草稿，请检查后保存。');close();}),'lde-button lde-primary'));
    });
    if(loaded.warnings.length)warn(loaded.warnings.join(' · '));
    if(new URL(location.href).searchParams.get('ai')==='page'&&config.lessonNo)ai('page');
    window.LanShareExplanation?.attach?.($('save'),{title:'保存与草稿',text:'编辑会自动保存；版本冲突时暂停。草稿仅存于当前浏览器，正式保存后其他课堂页面才会更新。'});
}
function historyReplace(url){window.history.replaceState(null,'',url);}
function renderHomeTarget(){
    if(store.model.kind!=='home')return;
    let node=$('home-target');if(!node){node=el('select');node.id='lde-home-target';node.setAttribute('aria-label','首页元素插入位置');$('search').parentElement.after(node);node.addEventListener('change',()=>store.ui.homeTarget=node.value);}
    node.replaceChildren(new Option('插入课程说明','page'));(store.model.tabs||[]).forEach((tab,index)=>node.append(new Option('插入标签：'+tab.label,'tab:'+index)));
    node.value=store.ui.homeTarget||'page';if(!node.value){node.value='page';store.ui.homeTarget='page';}
}
async function recoverCorrupt(revision){
    try{const entries=await api.history();dialog('从历史恢复文档',({body,foot,close})=>{
        body.append(el('p','lde-muted','当前原文档无法读取。恢复使用刚刚读取的文件版本作基线，其他人的新修改仍受版本检查保护。'));
        if(!entries.length)body.append(el('p','lde-dialog-error','没有可用历史版本。请回到材料库检查原始文件或从备份恢复。'));
        for(const entry of entries){const row=el('div','lde-list-row');row.append(el('span','',entry.created_at+' · '+entry.source),button('查看恢复稿',guard(async()=>{
            const snapshot=await api.revision(entry.id),operation=uid('restore');dialog('检查恢复稿',({body:b,foot:f,close:c})=>{
                const code=el('textarea','lde-code');code.readOnly=true;code.value=JSON.stringify(snapshot.document,null,2);b.append(code);
                for(const d of snapshot.diagnostics||[])b.append(el('p','lde-dialog-error',d.message));
                const status=el('p','lde-dialog-error');b.append(status);const restore=button('恢复此版本',async()=>{restore.disabled=true;try{await api.restore(entry.id,revision,operation);location.reload();}catch(e){status.textContent=e.message;restore.disabled=false;}},'lde-button lde-primary');
                f.append(button('下载恢复稿',()=>downloadJson(snapshot.document)),button('取消',c),restore);
            },{wide:true});
        })));body.append(row);}foot.append(button('关闭',close));
    });}catch(e){error(e);}
}
boot().catch(e=>{error(e);$('title').textContent='无法加载文档';$('context').textContent='请处理上方提示后重试';$('save-state').textContent='加载失败';for(const id of ['save','undo','redo','preview'])$(id).disabled=true;
    if(e.details?.revision){$('error').append(button('从历史恢复',()=>recoverCorrupt(e.details.revision)));$('history').addEventListener('click',()=>recoverCorrupt(e.details.revision));}
});
