import {clone,equal,locate} from './model.js';
import {jsonRequest} from './api.js';
import {openLessonDocPrompt} from '../lessondoc_prompt.js';
import {dialog,el,button,downloadJson,panelSection} from './ui.js';
import {EditorStore} from './state.js';
import {EditorBridge} from './bridge.js';

export function openAiPanel(store,api,mode,onError) {
    const element=mode==='selection'?locate(store.model,store.ui.selection[0])?.block:null;
    if(mode==='selection'&&!element)return;
    if(mode==='page'&&store.model.kind==='home')return;
    const before=clone(store.model),revision=store.revision,index=store.ui.slide,slide=store.model.slides?.[index];
    if(store.pending||store.retry||store.blocked){onError(new Error('请先完成保存或处理冲突，再使用 AI 改进。'));return;}
    openLessonDocPrompt({title:element?'AI 润色选中元素':'AI 改进当前页',featureKey:element?'lessondoc-element-polish':'lessondoc-slide-rewrite',
        description:element?'AI 仅改进内容，位置、尺寸、外观与动作由系统保留。结果先供预览，应用后可撤销。':'AI 根据当前修改生成候选页。结果先供预览，应用后可撤销。',
        submit:hint=>jsonRequest(api.base+'/ai-proposal'+api.query,'POST',{document:before,revision,slide_id:slide?.id||'',element_id:element?.id||'',user_hint:hint}),
        onSuccess:result=>{let previewBridge,closed=false;return dialog('检查 AI 改进结果',({body,foot,close})=>{
            const message=el('p','lde-muted',result.stale?'生成期间服务器正文已变化。候选结果可下载，处理版本冲突后再重新生成。':'检查候选内容后应用；正式保存会再次校验版本。');body.append(message);
            const oldText=el('textarea','lde-code'),newText=el('textarea','lde-code');oldText.readOnly=newText.readOnly=true;
            oldText.setAttribute('aria-label','原内容');newText.setAttribute('aria-label','AI 候选内容');
            const changed=element?locate(result.document,element.id)?.block:result.document.slides.find(s=>s.id===slide.id);
            oldText.value=JSON.stringify(element||slide,null,2);newText.value=JSON.stringify(changed,null,2);
            const previewState=new EditorStore(result.document,revision);previewState.ui.slide=index;previewState.ui.trial=true;
            const toggles=el('div','lde-inline-actions'),frame=el('iframe','lde-ai-preview'),status=el('p','lde-muted','正在加载候选预览…');frame.title='AI 候选页面预览';frame.src=api.previewUrl;
            const show=(document,label)=>{previewState.model=clone(document);previewBridge.render(true);previewBridge.api.previewActions(true);status.textContent=label;};
            const originalButton=button('查看原稿',()=>show(before,'原稿预览')),candidateButton=button('查看候选稿',()=>show(result.document,'AI 候选稿预览'));originalButton.disabled=candidateButton.disabled=true;toggles.append(originalButton,candidateButton);body.append(toggles,status,frame);
            previewBridge=new EditorBridge(frame,previewState,e=>status.textContent=e.message);
            previewBridge.connect().then(()=>{if(closed)return;previewBridge.api.previewActions(true);status.textContent='AI 候选稿预览';originalButton.disabled=candidateButton.disabled=false;}).catch(e=>{if(!closed)status.textContent=e.message;});
            const details=panelSection('查看结构化差异',{open:false}),compare=el('div','lde-compare');compare.append(oldText,newText);details.body.append(compare);body.append(details.root);
            if(result.warnings.length)body.append(el('p','lde-muted',result.warnings.join(' · ')));
            const error=el('p','lde-dialog-error');body.append(error);
            foot.append(button('下载候选稿',()=>downloadJson(result.document,'AI候选稿.json')),button('关闭',close));
            if(!result.stale)foot.append(button('应用到当前编辑稿',()=>{
                if(!equal(before,store.model)||store.revision!==revision){error.textContent='生成期间正文或版本已变化，请下载候选稿对照，或重新生成。';return;}
                store.command('应用 AI 改进',model=>{Object.keys(model).forEach(k=>delete model[k]);Object.assign(model,result.document);});close();
            },'lde-button lde-primary'));
        },{wide:true,onClose:()=>{closed=true;previewBridge?.destroy();}});}});
}
