import { apiFetch } from './api.js';
import { SignatureMultiSelect } from './signature_multi_select.js?v=material-workflows-1';

const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const refKey=doc=>`${doc.material_type}:${doc.material_id}`;
const ref=doc=>({material_type:doc.material_type,material_id:String(doc.material_id)});
const call=(path,options={})=>apiFetch('/api/signatures'+path,{...options,silent:true});
const post=(path,data)=>call(path,{method:'POST',body:JSON.stringify(data)});

export class MaterialSelectionPanel{
    constructor({grid,onChanged=()=>{}}){
        this.grid=grid;this.onChanged=onChanged;this.documents=[];this.visible=[];this.selected=new Set();this.sequence=0;this.pickers=[];this.context=null;
        const wrap=document.createElement('div');wrap.className='msw-material-layout';grid.before(wrap);wrap.append(grid);
        this.sidebar=document.createElement('aside');this.sidebar.className='msw-sidebar';this.sidebar.setAttribute('aria-label','文档属性与操作');wrap.append(this.sidebar);
        this.sidebar.innerHTML=`<div class="msw-eyebrow">文档属性与操作</div><h3 data-selection-title>选择文档</h3><p class="msw-note" data-selection-help>点击卡片空白处选择；可以同时处理多份文档。</p><dl data-common></dl><div class="msw-sidebar__actions"><button class="msw-button msw-primary" data-request-signatures hidden>申请签名</button><button class="msw-button" data-download-selected disabled>打包下载所选</button><button class="msw-button" data-select-visible>选择本页全部</button><button class="msw-button" data-clear-selection hidden>取消选择</button><button class="msw-button" data-download-all>全部打包下载</button></div><a class="msw-note" href="/manage/me/signature-workflows?view=outgoing">查看我的签名申请 →</a><div class="msw-status" role="status" data-progress></div>`;
        this.dialog=document.createElement('dialog');this.dialog.className='msw-dialog';document.body.append(this.dialog);
        grid.addEventListener('click',event=>{
            if(event.target.closest('a,button,input,select,textarea,label,[contenteditable],dialog'))return;
            if(window.getSelection()?.toString())return;
            const card=event.target.closest('[data-material-key]');if(card)this.toggle(card.dataset.materialKey);
        });
        grid.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.matches('[data-material-key]')){event.preventDefault();this.toggle(event.target.dataset.materialKey);}});
        this.sidebar.addEventListener('click',event=>{
            if(event.target.closest('[data-clear-selection]')){this.selected.clear();this.refresh();}
            if(event.target.closest('[data-select-visible]')){this.visible.forEach(doc=>this.selected.add(refKey(doc)));this.refresh();}
            if(event.target.closest('[data-request-signatures]'))this.openApplication();
            if(event.target.closest('[data-download-selected]'))this.download(this.selectedDocuments());
            if(event.target.closest('[data-download-all]'))this.download(this.visible);
        });
    }
    update(documents,visible=documents){
        this.documents=documents;this.visible=visible;
        const keys=new Set(documents.map(refKey));this.selected=new Set([...this.selected].filter(key=>keys.has(key)));
        this.refresh();
    }
    selectedDocuments(){return this.documents.filter(doc=>this.selected.has(refKey(doc)));}
    toggle(key){this.selected.has(key)?this.selected.delete(key):this.selected.add(key);this.refresh();}
    progress(text){this.sidebar.querySelector('[data-progress]').textContent=text;}
    async refresh(){
        const selected=this.selectedDocuments();
        this.grid.querySelectorAll('[data-material-key]').forEach(card=>{const active=this.selected.has(card.dataset.materialKey);card.classList.add('msw-card-selectable');card.classList.toggle('msw-selected-card',active);card.tabIndex=0;card.setAttribute('aria-label',`${active?'已选择':'未选择'}：${this.documents.find(doc=>refKey(doc)===card.dataset.materialKey)?.title||'文档'}，按空格切换选择`);});
        const $=selector=>this.sidebar.querySelector(selector);
        $('[data-selection-title]').textContent=selected.length?`已选 ${selected.length} 份文档`:'选择文档';
        const hidden=selected.filter(doc=>!this.visible.some(item=>refKey(item)===refKey(doc))).length;
        $('[data-selection-help]').textContent=hidden?`其中 ${hidden} 份位于其他筛选结果中。`:'点击卡片空白处选择或取消。';
        $('[data-clear-selection]').hidden=!selected.length;$('[data-download-selected]').disabled=!selected.length;
        $('[data-download-all]').disabled=!this.visible.length;$('[data-download-all]').textContent=`全部打包下载（当前筛选 ${this.visible.length} 份）`;
        $('[data-select-visible]').disabled=!this.visible.length;
        const signature=JSON.stringify(selected.map(doc=>[refKey(doc),doc.updated_at]));
        if(signature===this.contextSignature)return;
        this.contextSignature=signature;const sequence=++this.sequence;
        $('[data-request-signatures]').hidden=true;$('[data-common]').innerHTML='';this.context=null;
        if(!selected.length)return;
        try{
            const context=await post('/materials/selection',{documents:selected.map(ref)});if(sequence!==this.sequence)return;
            this.context=context;
            $('[data-common]').innerHTML=Object.entries(context.common_properties).map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(value||'—')}</dd></div>`).join('')+`<div><dt>完成情况</dt><dd>${context.documents.filter(doc=>doc.complete).length} 份可提交 / ${context.documents.filter(doc=>!doc.complete).length} 份待补充</dd></div>`;
            $('[data-request-signatures]').hidden=!context.can_apply;
            if(!context.can_apply)$('[data-selection-help]').textContent=context.apply_reason;
        }catch(error){if(sequence===this.sequence){this.contextSignature=null;this.progress(error.message);}}
    }
    async openApplication(){
        if(!this.context?.can_apply)return;
        const dialogSequence=this.dialogSequence=(this.dialogSequence||0)+1;
        this.pickers.forEach(picker=>picker.destroy());this.pickers=[];
        const context=this.context;const docs=context.documents.map(ref);const first=docs[0];const config=[];const key=crypto.randomUUID();
        this.dialog.innerHTML=`<header class="msw-dialog__head"><div><span class="msw-eyebrow">${docs.length} 份同类文档</span><h3>申请并配置签名</h3></div><button class="msw-button" data-close aria-label="关闭">×</button></header><div class="msw-dialog__body"><p class="msw-note">选择签名后，可在下拉框的“已选”中调整顺序。</p><div data-points></div><label class="msw-field">申请附言<textarea data-note rows="2" maxlength="300" placeholder="说明材料用途，帮助审批人了解背景"></textarea></label><label class="msw-inline"><input type="checkbox" data-auto checked>每个位置全部获批后，自动按选定顺序更新文档</label></div><footer class="msw-dialog__foot"><span class="msw-status" data-result role="status">正在读取签名位置…</span><button class="msw-button msw-primary" data-submit disabled>提交 ${docs.length} 份申请</button></footer>`;
        this.dialog.querySelector('[data-close]').onclick=()=>this.dialog.close();this.dialog.showModal();
        try{
            const states=await Promise.all(context.points.map(point=>call(`/points/${encodeURIComponent(point.key)}/state?${new URLSearchParams(first)}`)));
            if(!this.dialog.open||dialogSequence!==this.dialogSequence)return;
            const container=this.dialog.querySelector('[data-points]');container.style.display='grid';container.style.gap='16px';
            context.points.forEach((point,index)=>{
                const current={key:point.key,signature_ids:[],mode:'append',opinion_mode:'keep'};config.push(current);
                const card=document.createElement('section');card.className='msw-point';
                card.innerHTML=`<header><strong>${esc(point.label)}</strong></header><div data-picker></div><div class="msw-inline"><label>已有签名 <select data-mode><option value="append">保留并追加</option><option value="replace">替换为本次选择</option></select></label>${point.opinion_key?'<label>批语 <select data-opinion><option value="keep">保留当前批语</option><option value="stamp">使用所选特殊签名</option><option value="clear">留空</option></select></label>':''}</div>`;
                container.append(card);card.querySelector('[data-mode]').onchange=event=>current.mode=event.target.value;
                if(point.opinion_key)card.querySelector('[data-opinion]').onchange=event=>current.opinion_mode=event.target.value;
                const picker=new SignatureMultiSelect({root:card.querySelector('[data-picker]'),items:states[index].signatures,identityLabels:states[index].point.required_identity_labels,
                    onChange:ids=>{current.signature_ids=ids;if(point.opinion_key&&ids.some(id=>picker.items.find(item=>item.id===id)?.signature_kind==='stamp')){current.opinion_mode='stamp';card.querySelector('[data-opinion]').value='stamp';}},
                    onSearch:async q=>{const result=await call(`/points/${encodeURIComponent(point.key)}/state?${new URLSearchParams({...first,q})}`);return result.signatures;}});
                this.pickers.push(picker);
            });
            this.dialog.querySelector('[data-result]').textContent='未选择签名的位置保持现状。';
            const submit=this.dialog.querySelector('[data-submit]');submit.disabled=false;
            submit.onclick=async()=>{
                const selected=config.filter(point=>point.signature_ids.length);if(!selected.length){this.dialog.querySelector('[data-result]').textContent='请至少为一个位置选择签名。';return;}
                submit.disabled=true;
                try{
                    const result=await post('/materials/applications',{documents:docs,points:selected,note:this.dialog.querySelector('[data-note]').value,auto_apply:this.dialog.querySelector('[data-auto]').checked,idempotency_key:key});
                    this.dialog.close();this.progress('正在准备申请文档，完成后通知审批人…');this.watchApplication(result.id);
                }catch(error){this.dialog.querySelector('[data-result]').textContent=error.message;submit.disabled=false;}
            };
        }catch(error){if(dialogSequence===this.dialogSequence)this.dialog.querySelector('[data-result]').textContent=error.message;}
    }
    async watchApplication(id){
        try{
            const result=await call(`/materials/applications/${id}`);
            if(['completed','partial','failed'].includes(result.status)){
                const errors=result.results.filter(item=>item.status==='failed');
                this.progress(`已提交 ${result.results.length-errors.length} 份材料。${result.error_message||''}${errors.length?'\n'+errors.map(item=>item.title+'：'+item.error).join('\n'):''}`);this.onChanged();return;
            }
            this.progress(`已准备 ${result.results.length} 份材料，正在继续…`);setTimeout(()=>this.watchApplication(id),2500);
        }catch(error){this.progress(error.message+' 可在“我的申请”查看已提交材料。');}
    }
    async download(documents){
        if(!documents.length)return;
        this.progress('正在核对打包文档…');
        try{
            const plan=await post('/materials/bundles/preflight',{documents:documents.map(ref)});
            let confirmed=true;
            if(plan.incomplete.length)confirmed=await this.confirmIncomplete(plan.incomplete);
            if(!confirmed){this.progress('已取消打包。');return;}
            await post(`/materials/bundles/${plan.id}/submit`,{allow_incomplete:plan.incomplete.length>0});this.watchBundle(plan.id);
        }catch(error){this.progress(error.message);}
    }
    confirmIncomplete(titles){
        const dialog=document.createElement('dialog');dialog.className='msw-dialog';
        dialog.innerHTML=`<header class="msw-dialog__head"><h3>以下文档内容待补充</h3></header><div class="msw-dialog__body"><p class="msw-note">继续后，这些文档会按当前内容一同打包。</p><ul>${titles.map(title=>`<li>${esc(title)}</li>`).join('')}</ul></div><footer class="msw-dialog__foot"><button class="msw-button" data-cancel>取消</button><button class="msw-button msw-primary" data-continue>继续打包全部文档</button></footer>`;
        document.body.append(dialog);dialog.showModal();
        return new Promise(resolve=>{let proceed=false;dialog.querySelector('[data-continue]').onclick=()=>{proceed=true;dialog.close();};dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{dialog.remove();resolve(proceed);},{once:true});});
    }
    async watchBundle(id){
        try{
            const result=await call(`/materials/bundles/${id}`);
            if(result.status==='ready'){
                this.progress(result.error_message||`已打包 ${result.results.length} 份文档。`);
                const link=document.createElement('a');link.className='msw-button msw-primary';link.href=result.download_url;link.textContent='下载压缩包';
                this.sidebar.querySelector('[data-progress]').append(document.createElement('br'),link);link.click();return;
            }
            if(result.status==='failed'){this.progress(result.results.map(item=>item.title+'：'+item.error).join('\n')||result.error_message||'打包失败。');return;}
            this.progress(`后台打包中 · 已处理 ${result.results.length} 份`);setTimeout(()=>this.watchBundle(id),2000);
        }catch(error){this.progress(error.message);}
    }
}
