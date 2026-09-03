import {clone,locate,uid,walkBlocks,removeBlocks} from './model.js';
import {dialog,el,button,field} from './ui.js';
import {mergeCanonical} from './state.js';

const properties='display position top right bottom left width height min-width max-width box-sizing padding margin color background-color background-image font-family font-size font-weight font-style line-height letter-spacing text-align white-space vertical-align border border-radius box-shadow overflow flex-direction flex-wrap align-items align-self justify-content gap grid-template-columns grid-template-rows grid-area transform transform-origin object-fit fill stroke stroke-width'.split(' ');
const trivial=new Set(['normal','none','auto','static','0px','0px 0px','rgba(0, 0, 0, 0)']);

export function snapshotPage(bridge){
    const source=bridge.api.slideEl();if(!source)throw new Error('当前页尚未渲染完成。');
    const doc=bridge.document;
    function mirror(node){
        if(node.nodeType===3)return doc.createTextNode(node.textContent);
        if(node.nodeType!==1)return null;
        if(node.matches('.ld-bg,.ld-global,[data-ld-gid],.slide-notes,.slide-title,.slide-sub,.slide-chrome-head,.slide-chrome-foot,.ld-edit-layer,script,style')||node.closest('[data-lde-handle]'))return null;
        const tag=node.tagName.toLowerCase();if(['audio','video','input','textarea','select'].includes(tag))return null;
        const copy=doc.createElementNS(node.namespaceURI,tag==='button'?'span':tag);
        for(const name of ['src','href','alt','viewBox','d','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','width','height','points','fill','stroke','id','marker-end','refX','refY','markerWidth','markerHeight','xmlns'])if(node.hasAttribute(name))copy.setAttribute(name,node.getAttribute(name));
        const computed=bridge.window.getComputedStyle(node);
        const styles=properties.map(name=>[name,computed.getPropertyValue(name)]).filter(([,value])=>value&&!trivial.has(value)&&!value.includes('url(')).map(([name,value])=>name+':'+value);
        copy.setAttribute('style',styles.join(';'));
        for(const child of node.childNodes){const next=mirror(child);if(next)copy.append(next);}return copy;
    }
    const root=doc.createElement('div'),computed=bridge.window.getComputedStyle(source);
    root.setAttribute('style','position:relative;box-sizing:border-box;width:1280px;height:720px;color:'+computed.color);
    for(const node of source.children){
        if(['H1','H2'].includes(node.tagName)||node.matches('.lesson-badge,.title-sub,.course-name,.sec-no,.sec-title'))continue;
        const copy=mirror(node);if(copy){
            // Preserve the content's measured origin when platform chrome is
            // removed; its former flex space must not shift the captured body.
            copy.setAttribute('style',(copy.getAttribute('style')||'')+';position:absolute;left:'+node.offsetLeft+'px;top:'+node.offsetTop+'px;width:'+node.offsetWidth+'px;height:'+node.offsetHeight+'px;margin:0');root.append(copy);
        }
    }
    const body=root.outerHTML;if(body.length>20000)throw new Error('此页静态内容超过 HTML 片段的 20,000 字符限额。请先拆分页面，或逐个编辑 HTML 元素。');
    return body;
}

export function openHtmlEditor(store,api,config,bridge,{convert=false}={}) {
    const before=clone(store.model),serial=store.serial,index=store.ui.slide,item=locate(before,store.ui.selection[0]);
    if(!convert&&item?.block.type!=='html')return;
    let value=convert?{id:uid(),type:'html',body:snapshotPage(bridge),css:'',frame:{x:0,y:0,w:1280,h:720},natural:{w:1280,h:720}}:clone(item.block),checked=null,inputVersion=0;
    dialog(convert?'将本页转换为静态 HTML':'编辑 HTML 片段',({body,foot,close})=>{
        body.append(el('p','lde-muted',convert?'将当前显示内容合并为一个 HTML 元素。播放器、按钮动作、隐藏状态和运行过程会变为静态；正文标题、页面背景及全局元素保留。应用后可撤销，保存后可从历史恢复。':'HTML 和 CSS 先经过安全预检，预览显示规范化后的内容。应用后仍使用同一文档模型保存。'));
        const changed=(key,v)=>{value[key]=v;inputVersion++;checked=null;apply.disabled=true;};
        body.append(field('HTML 内容',value.body,v=>changed('body',v),{multiline:true,rows:8}),field('局部 CSS',value.css||'',v=>changed('css',v),{multiline:true,rows:4}));
        const status=el('p','lde-muted'),preview=el('iframe','lde-html-preview');preview.title='净化后的 HTML 预览';preview.setAttribute('sandbox','');body.append(status,preview);
        const apply=button(convert?'确认静态转换':'应用 HTML',()=>{
            if(!checked||serial!==store.serial){status.textContent='正文已变化，请重新打开面板并预检。';return;}
            const document=mergeCanonical(before,store.model,checked.document);
            store.command(convert?'静态 HTML 转换':'编辑 HTML',model=>{Object.keys(model).forEach(k=>delete model[k]);Object.assign(model,document);});store.select([value.id]);close();
        },'lde-button lde-primary');apply.disabled=true;
        foot.append(button('取消',close),button('预检并预览',async()=>{
            try{
                const version=inputVersion;
                if(serial!==store.serial)throw new Error('正文已变化，请重新打开面板。');
                const candidate=clone(before);
                if(convert){const slide=candidate.slides[index],ids=[];walkBlocks(slide,b=>ids.push(b.id));removeBlocks(candidate,ids);
                    for(const key of ['blocks','left','right','areas','objects','overlays','summary','nextUp','hint'])delete slide[key];slide.layout='canvas';slide.objects=[clone(value)];delete slide.empty;
                }else Object.assign(locate(candidate,value.id).block,value);
                const result=await api.validate(mergeCanonical(before,store.model,candidate));if(!result.valid)throw new Error(result.warnings.join('\n'));
                if(version!==inputVersion)throw new Error('代码已变化，请再次预检。');
                const normalized=locate(result.document,value.id)?.block;if(!normalized)throw new Error('此 HTML 内容无法保存。');
                const base='/materials/render/'+config.rootMaterialId+'/'+(config.lessonNo?'lesson_'+config.lessonNo+'/':'');
                preview.srcdoc='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><base href="'+base+'"><style>body{margin:12px;font-family:sans-serif}'+normalized.css+'</style></head><body>'+normalized.body+'</body></html>';
                checked=result;status.textContent=result.warnings.join(' · ')||'预检通过，请检查预览后应用。';apply.disabled=false;
            }catch(e){status.textContent=e.message;apply.disabled=true;}
        }),apply);
    },{wide:true});
}
