import {request,jsonRequest} from './api.js';
import {dialog,el,button,field} from './ui.js';

export function openMedia(config,onPick,onError) {
    dialog('包内素材',({body,foot,close})=>{
        const input=el('input');input.type='file';input.accept='image/png,image/jpeg,image/gif,image/webp,image/svg+xml,audio/mpeg,audio/wav,audio/ogg,audio/mp4,video/mp4,video/webm';
        const status=el('p','lde-muted','图片 8 MiB · 音频 20 MiB · 视频 100 MiB。素材会保留在当前学习文档包内。'),grid=el('div','lde-media-grid'),more=button('加载更多',()=>load(cursor));body.append(input,status,grid,more);
        let cursor=0,busy=false;
        const choose=item=>{onPick(item);close();};
        const append=item=>{const b=button('',()=>choose(item),'lde-button lde-media-card');if(item.kind==='image'){const img=el('img');img.src=item.preview_url;img.alt='';img.loading='lazy';b.append(img);}else b.append(el('strong','',item.kind==='video'?'视频':'音频'));b.append(el('span','',item.name));grid.append(b);};
        const load=async(after=0)=>{try{const result=await request('/api/lessondoc/editor/packs/'+config.packId+'/media?lesson_no='+config.lessonNo+'&after_id='+after);for(const item of result.items)append(item);cursor=result.next_cursor;more.hidden=!cursor;}catch(error){status.textContent=error.message;onError(error);}};
        input.addEventListener('change',async()=>{
            const file=input.files[0];if(!file||busy)return;busy=true;input.disabled=true;status.textContent='正在上传并校验 '+file.name+'…';
            try{const item=await request('/api/lessondoc/editor/packs/'+config.packId+'/media?lesson_no='+config.lessonNo+'&filename='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});append(item);status.textContent='上传完成，点击素材即可插入。'+(item.warnings||[]).join(' ');}catch(error){status.textContent=error.message;}finally{busy=false;input.disabled=false;input.value='';}
        });foot.append(button('关闭',close));load();
    },{wide:true});
}
export function openTemplates(config,onInsert,onError) {
    dialog('我的元素',({body,foot,close})=>{
        const list=el('div');body.append(el('p','lde-muted','元素与引用素材可跨文档复用。修改或删除模板不会改变已经插入的实例。'),list);
        let cursor=0;const more=button('加载更多',()=>load(cursor));body.append(more);
        const load=async(before=0)=>{try{const result=await request('/api/lessondoc/editor/custom-elements?before_id='+before);for(const item of result.items){const row=el('div','lde-list-row'),name=el('strong','',item.name);row.append(name,button('插入',async()=>{try{const result=await jsonRequest('/api/lessondoc/editor/custom-elements/'+item.id+'/insert','POST',{pack_id:config.packId,lesson_no:config.lessonNo});onInsert(result.element);close();}catch(error){onError(error);}}),button('改名',()=>{dialog('元素名称',({body:b,foot:f,close:c})=>{let value=item.name;b.append(field('名称',value,v=>value=v));f.append(button('保存',async()=>{try{await jsonRequest('/api/lessondoc/editor/custom-elements/'+item.id,'PUT',{name:value});item.name=value;name.textContent=value;c();}catch(error){onError(error);}}));});}),button('删除模板',async()=>{try{await request('/api/lessondoc/editor/custom-elements/'+item.id,{method:'DELETE'});row.remove();}catch(error){onError(error);}}));list.append(row);}cursor=result.next_cursor;more.hidden=!cursor;if(!list.children.length)list.append(el('p','lde-muted','选择一个元素后，在右侧选择“保存为我的元素”。'));}catch(error){onError(error);}};
        load();foot.append(button('关闭',close));
    });
}
