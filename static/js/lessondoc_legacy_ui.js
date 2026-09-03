import {request,jsonRequest} from './lessondoc_editor/api.js';
import {dialog,el,button,field} from './lessondoc_editor/ui.js';
import {ensureEditorStyles} from './lessondoc_prompt.js';

export async function openLegacyConversion(rootId) {
    ensureEditorStyles();
    const context=await request('/api/lessondoc/editor/legacy-context/'+rootId);
    dialog('转换旧版学习文档',({body,foot,close})=>{
        body.append(el('p','lde-muted','转换会创建新的学习文档包。先检查解析结果和缺失提示，再决定创建；原有文档与课堂绑定保留。'));
        let course='',name='';const options={'':'请选择课程'};context.courses.forEach(c=>options[c.id]=c.name);
        body.append(field('所属课程',course,v=>course=v,{choices:options}),field('新文档包名称（可选）',name,v=>name=v));
        const error=el('p','lde-dialog-error');error.setAttribute('role','alert');body.append(error);
        if(!context.courses.length){body.append(el('p','lde-muted','请先到课程管理创建或关联课程。'));return;}
        const check=button('预检转换结果',async()=>{
            if(!course){error.textContent='请选择所属课程。';return;}check.disabled=true;error.textContent='';
            const payload={root_material_id:rootId,course_id:Number(course),pack_name:name,dry_run:true};
            try{const preview=await jsonRequest('/api/lessondoc/packs/import-legacy','POST',payload);close();showPreview(payload,preview);}catch(e){error.textContent=e.message;}finally{check.disabled=false;}
        },'lde-button lde-primary');foot.append(button('取消',close),check);
    });
}
function showPreview(payload,preview){
    dialog('检查旧文档解析结果',({body,foot,close})=>{
        body.append(el('p','',`可解析 ${preview.lesson_count} 个课次。请检查提示后创建新包。`));
        for(const lesson of preview.preview.lessons||[])body.append(el('p','lde-muted','第 '+lesson.n+' 课 · '+lesson.title));
        const warnings=el('ul');for(const text of preview.warnings||[])warnings.append(el('li','',text));body.append(warnings);
        const error=el('p','lde-dialog-error');body.append(error);
        const create=button('确认创建新文档包',async()=>{
            create.disabled=true;create.textContent='正在转换…';
            try{const result=await jsonRequest('/api/lessondoc/packs/import-legacy','POST',{...payload,dry_run:false});close();showResult(result);}
            catch(e){error.textContent=e.message;create.disabled=false;create.textContent='确认创建新文档包';}
        },'lde-button lde-primary');foot.append(button('返回',()=>{close();openLegacyConversion(payload.root_material_id);}),create);
    });
}
function showResult(result){
    dialog('新文档包已创建',({body,foot,close})=>{
        body.append(el('p','',result.message));for(const warning of result.warnings||[])body.append(el('p','lde-muted',warning));
        const bindings=el('div'),error=el('p','lde-dialog-error');body.append(el('p','lde-muted','可选：将新包添加到课堂。未勾选时保持现有课堂安排。'),bindings,error);
        const chosen=new Set(),bind=button('添加到已选课堂',async()=>{
            if(!chosen.size){error.textContent='请先选择课堂，或直接进入编辑器。';return;}bind.disabled=true;
            try{await jsonRequest('/api/lessondoc/packs/'+result.pack.id+'/bind','POST',{class_offering_ids:[...chosen]});error.textContent='绑定完成。';}catch(e){error.textContent=e.message;}finally{bind.disabled=false;}
        });
        request('/api/materials/'+result.pack.root_material_id+'/learning-bindings').then(data=>{for(const o of data.offerings||[])bindings.append(field(o.course_name+' · '+o.class_name,false,v=>{if(v)chosen.add(o.id);else chosen.delete(o.id);},{type:'checkbox'}));}).catch(e=>error.textContent=e.message);
        foot.append(button('关闭',close),bind,button('进入编辑器',()=>location.href='/materials/lessondoc-editor/'+result.pack.id+'?lesson=0','lde-button lde-primary'));
    });
}
