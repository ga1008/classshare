import {at,clone,equal,locate,setAt,freshInstance,insertionList} from './model.js';
import {REGISTRY,FIELD_LABELS,FIELD_OPTIONS,contentFields,THEMES} from './registry.js';
import {el,button,field,panelSection} from './ui.js';
import {changeGlobal,alignSelection,flowDestinations,moveFlow} from './object_commands.js';
import {renderTable,renderQuiz,renderCodewalk,DIAGRAM_DEFAULTS} from './content_controls.js';

export class PropsPanel {
    constructor(root,store,callbacks) {this.root=root;this.store=store;this.callbacks=callbacks;this.selection='';}
    updateSelected(path,value,label='修改属性') {
        const ids=[...this.store.ui.selection];
        this.store.command(label,model=>{for(const id of ids){const item=locate(model,id);if(item)setAt(item.block,path,clone(value));}},{coalesce:ids.join(',')+':'+path.join('.')});
    }
    render(force=false) {
        const key=this.store.ui.selection.join(',')+':'+this.store.ui.slide;
        if(!force&&key===this.selection&&this.root.contains(document.activeElement))return;
        this.selection=key;this.root.replaceChildren();
        const selected=this.store.ui.selection.map(id=>locate(this.store.model,id)).filter(Boolean);
        if(!selected.length){this.renderPage();return;}
        const blocks=selected.map(x=>x.block),first=blocks[0];
        const head=el('div','lde-panel-heading');head.append(el('h2','',blocks.length===1?(first.name||REGISTRY[first.type]?.label||first.type):'已选 '+blocks.length+' 个元素'));
        this.root.append(head);
        const controls=el('div','lde-inline-actions');controls.append(button('复制',()=>this.callbacks.copy()),button('删除',()=>this.callbacks.remove()),button('高级',()=>this.callbacks.source('selection')));this.root.append(controls);
        if(blocks.length===1&&!first.frame&&Array.isArray(at(this.store.model,selected[0].path.slice(0,-1)))){
            for(const[offset,label]of[[-1,'上移'],[1,'下移']])controls.append(button(label,()=>{this.store.command('调整内容顺序',model=>{const item=locate(model,first.id),list=at(model,item.path.slice(0,-1)),index=item.path.at(-1),next=index+offset;if(next>=0&&next<list.length)[list[index],list[next]]=[list[next],list[index]];});this.render(true);}));
            if(this.store.model.kind==='home'){
                const destinations={page:'课程说明'};(this.store.model.tabs||[]).forEach((tab,index)=>destinations['tab:'+index]='标签：'+tab.label);
                controls.append(field('移动至',selected[0].path[0]==='tabs'?'tab:'+selected[0].path[1]:'page',value=>{this.store.command('移动首页内容',model=>{const item=locate(model,first.id),list=at(model,item.path.slice(0,-1));list.splice(item.path.at(-1),1);insertionList(model,0,value).push(item.block);});this.render(true);},{choices:destinations}));
            }else{
                const destinations=flowDestinations(this.store.model,this.store.ui.slide),choices=Object.fromEntries(destinations.map(d=>[JSON.stringify(d.path),d.label]));
                if(destinations.length)controls.append(field('移动至',JSON.stringify(selected[0].path.slice(0,-1)),value=>{const destination=destinations.find(d=>JSON.stringify(d.path)===value);if(!destination)return;this.store.command('移动分栏内容',model=>moveFlow(model,first.id,destination.path));this.render(true);},{choices}));
            }
        }
        if(blocks.length>1&&blocks.every(b=>b.frame))controls.append(button('组合',()=>this.callbacks.group()));
        if(blocks.length===1&&first.type==='group')controls.append(button('拆组',()=>this.callbacks.ungroup()));
        if(blocks.length>1&&blocks.every(b=>b.frame)){
            const alignment=panelSection('对齐与分布');this.root.append(alignment.root);alignment.body.classList.add('lde-inline-actions');
            for(const[mode,label]of[['left','左对齐'],['center','水平居中'],['right','右对齐'],['top','顶对齐'],['middle','垂直居中'],['bottom','底对齐'],['distributeX','水平分布'],['distributeY','垂直分布']])alignment.body.append(button(label,()=>{try{this.store.command(label,model=>alignSelection(model,this.store.ui.selection,mode));}catch(error){this.callbacks.error(error);}}));
        }
        if(this.store.ui.groupPath.length)this.root.append(button('退出组合层级',()=>{this.store.ui.groupPath.pop();this.store.select([]);}));
        const add=(body,label,path,options={})=>{
            const values=blocks.map(b=>at(b,path)),mixed=values.some(v=>!equal(v,values[0]));
            body.append(field(label,mixed?null:values[0],v=>this.updateSelected(path,v,label),{...options,mixed}));
        };
        const identity=panelSection('元素');this.root.append(identity.root);
        if(blocks.length===1)add(identity.body,'名称',['name'],{live:true});add(identity.body,'初始隐藏',['hidden'],{type:'checkbox'});
        if(this.store.model.kind!=='home'&&selected.every(x=>x.block.frame&&!x.ancestors.length)){
            const allGlobal=selected.every(x=>x.path[0]==='globals');
            identity.body.append(button(allGlobal?'只保留在本页':'设为全局元素',()=>{try{this.store.command('调整全局范围',model=>changeGlobal(model,this.store.ui.selection,!allGlobal,this.store.ui.slide));this.render(true);}catch(error){this.callbacks.error(error);}}));
            if(allGlobal){identity.body.append(el('p','lde-muted','全局元素共享同一份内容。修改和删除会影响整课。'));add(identity.body,'跳过封面与章节页',['skipCovers'],{type:'checkbox'});
                const sid=this.store.model.slides[this.store.ui.slide].id;
                identity.body.append(field('在本页排除',blocks.every(b=>b.excludeSlides?.includes(sid)),value=>this.store.command('调整本页全局显隐',model=>{for(const id of this.store.ui.selection){const b=locate(model,id).block,ids=new Set(b.excludeSlides||[]);if(value)ids.add(sid);else ids.delete(sid);b.excludeSlides=[...ids];}}),{type:'checkbox'}));
            }
        }
        if(blocks.every(b=>b.frame)) {
            const pos=panelSection('位置与大小');this.root.append(pos.root);pos.body.classList.add('lde-field-grid');
            for(const[k,t,min,max]of[['x','横坐标',-200,1480],['y','纵坐标',-200,920],['w','宽度',8,1680],['h','高度',8,1680],['r','旋转角度',-180,180],['z','叠放顺序',-100,1000]])add(pos.body,t,['frame',k],{type:'number',min,max});
        }
        const style=panelSection('文字与外观',{open:false});this.root.append(style.root);
        add(style.body,'字体',['style','font'],{choices:{sans:'无衬线',serif:'衬线',kai:'楷体',mono:'等宽',rounded:'圆体'}});
        add(style.body,'字号',['style','size'],{type:'number',min:12,max:160});add(style.body,'字重',['style','weight'],{choices:{400:'常规',500:'中等',600:'半粗',700:'粗体',800:'特粗'}});
        add(style.body,'文字颜色',['style','color']);add(style.body,'背景颜色',['style','bg']);add(style.body,'对齐',['style','align'],{choices:{left:'左对齐',center:'居中',right:'右对齐'}});
        add(style.body,'内边距',['style','padding'],{type:'number',min:0,max:120});add(style.body,'不透明度',['style','opacity'],{type:'number',min:0,max:1,step:.05});
        add(style.body,'圆角',['style','border','radius'],{type:'number',min:0,max:120});add(style.body,'边框宽度',['style','border','width'],{type:'number',min:0,max:12});add(style.body,'边框颜色',['style','border','color']);
        add(style.body,'斜体',['style','italic'],{type:'checkbox'});add(style.body,'行高',['style','lineHeight'],{type:'number',min:.9,max:3,step:.1});add(style.body,'字距',['style','letterSpacing'],{type:'number',min:-2,max:20,step:.1});
        add(style.body,'阴影',['style','shadow'],{choices:{none:'无',soft:'柔和',hard:'硬阴影',glow:'发光'}});
        add(style.body,'描边颜色',['style','stroke','color']);add(style.body,'描边宽度',['style','stroke','width'],{type:'number',min:0,max:6});
        for(const[key,title]of[['gradient','文字渐变'],['bgGradient','背景渐变']]){const part=panelSection(title,{open:false});style.body.append(part.root);add(part.body,'起始颜色',['style',key,'from']);add(part.body,'结束颜色',['style',key,'to']);add(part.body,'角度',['style',key,'angle'],{type:'number',min:0,max:360});}
        style.body.append(button('重置外观',()=>{this.store.command('重置元素外观',model=>{for(const id of this.store.ui.selection)delete locate(model,id).block.style;});this.render(true);}));
        if(blocks.length===1) {
            const content=panelSection('内容');this.root.append(content.root);
            this.root.insertBefore(content.root,identity.root);
            const commit=(label,mutate)=>this.store.command(label,model=>mutate(locate(model,first.id).block),{coalesce:first.id+':'+label});
            const renderer={table:renderTable,quiz:renderQuiz,codewalk:renderCodewalk}[first.type];
            if(first.type==='html')content.body.append(button('打开 HTML 编辑与预览',()=>this.callbacks.html(false)));
            else if(renderer)renderer(content.body,first,commit,()=>this.render(true),this.callbacks.error);
            else for(const name of contentFields(first))this.structured(content.body,first[name]??REGISTRY[first.type].defaults[name],[name],first.id,FIELD_LABELS[name]||name,0);
            if(first.type==='media')content.body.append(button('从素材库更换',()=>this.callbacks.media(first.id)));
            const advanced=el('div','lde-inline-actions');advanced.append(button('动作',()=>this.callbacks.actions()),button('保存为我的元素',()=>this.callbacks.template()));
            if(!['group','tabs','details','stepper'].includes(first.type))advanced.append(button('AI 润色',()=>this.callbacks.ai('selection')));this.root.append(advanced);
        }
    }
    structured(parent,value,path,id,label,depth) {
        if(depth>6){parent.append(el('p','lde-muted','更多层级可在高级 JSON 中编辑。'));return;}
        const change=(v)=>this.updateSelected(path,v,'修改'+label);
        if(Array.isArray(value)) {
            const part=panelSection(label,{open:depth<2});parent.append(part.root);
            value.slice(0,60).forEach((item,index)=>{
                const row=el('div','lde-array-item');part.body.append(row);
                if(item?.type&&REGISTRY[item.type])row.append(button(item.name||REGISTRY[item.type].label,()=>this.store.select([item.id])));
                else this.structured(row,item,[...path,index],id,'第 '+(index+1)+' 项',depth+1);
                const tools=el('div','lde-inline-actions');tools.append(button('上移',()=>this.arrayCommand(id,path,index,'up')),button('下移',()=>this.arrayCommand(id,path,index,'down')),button('移除',()=>this.arrayCommand(id,path,index,'remove')));row.append(tools);
            });
            if(value.length>60)part.body.append(el('p','lde-muted','超过 60 项的内容请使用高级 JSON 编辑。'));
            part.body.append(button('添加一项',()=>{
                this.store.command('添加'+label,model=>{const target=locate(model,id),array=at(target.block,path);let item=clone(value.at(-1)??'新条目');if(item&&typeof item==='object')item=freshInstance(item);array.push(item);});this.render(true);
            }));return;
        }
        if(value&&typeof value==='object') {
            if(value.type&&REGISTRY[value.type]){parent.append(button(REGISTRY[value.type].label+' · 编辑内容',()=>this.store.select([value.id])));return;}
            const part=panelSection(label,{open:depth<2});parent.append(part.root);
            for(const[key,val]of Object.entries(value))this.structured(part.body,val,[...path,key],id,FIELD_LABELS[key]||key,depth+1);return;
        }
        const multiline=['md','text','code','output','body','css','out','note','explain','q','summary'].includes(path.at(-1));
        let choices=FIELD_OPTIONS[path.at(-1)];
        if(path.at(-1)==='kind'){
            const type=locate(this.store.model,id)?.block.type;
            choices=type==='media'?{image:'图片',audio:'音频',video:'视频'}:null;
            if(type==='diagram'){
                parent.append(field('图示类型',value,kind=>{this.store.command('更换图示类型',model=>{const b=locate(model,id).block;for(const key of ['nodes','edges','actors','messages','layers','links','root','children'])delete b[key];Object.assign(b,clone(DIAGRAM_DEFAULTS[kind]),{kind});});this.render(true);},{choices:{flow:'流程图',sequence:'时序图',arch:'架构图',mindmap:'思维导图'}}));
                parent.append(el('p','lde-muted','切换类型会替换为该类型的示例结构，可撤销。'));return;
            }
        }
        parent.append(field(label,value,change,{multiline,live:multiline,type:typeof value==='boolean'?'checkbox':typeof value==='number'?'number':'text',choices}));
    }
    arrayCommand(id,path,index,operation) {
        try{this.store.command('调整条目',model=>{
            const block=locate(model,id).block,array=at(block,path);
            if(path.at(-1)==='lines'&&block.type==='codewalk'&&operation==='remove') {
                const removed=array[index],source=array.slice(0,index).filter(x=>typeof x==='string'||x.code!=null).length;
                array.splice(index,1);
                if(typeof removed==='string'||removed.code!=null)for(let i=array.length-1;i>=0;i--){const line=array[i];if(line.ref===source&&line.code==null)array.splice(i,1);else if(line.ref>source&&line.code==null)line.ref--;}
            }else if(path.at(-1)==='lines'&&block.type==='codewalk'&&operation!=='remove')throw new Error('执行轨迹含行引用，请在高级 JSON 中调整次序并预检引用。');
            else if(operation==='remove')array.splice(index,1);
            else{const next=index+(operation==='up'?-1:1);if(next>=0&&next<array.length)[array[index],array[next]]=[array[next],array[index]];}
        });this.render(true);}catch(error){this.callbacks.error(error);}
    }
    renderPage() {
        const model=this.store.model,home=model.kind==='home',slide=home?null:model.slides[this.store.ui.slide];
        this.root.append(el('h2','lde-panel-heading',home?'课程首页':'页面设置'));
        const write=(path,value)=>this.store.command('修改页面',d=>setAt(d,path,value),{coalesce:path.join('.')});
        this.root.append(field(home?'展示课程名称':'课次标题',home?model.course.name:model.title,v=>write(home?['course','name']:['title'],v),{live:true}));
        this.root.append(field('主题',model.theme||'sky',v=>write(['theme'],v),{choices:THEMES}));
        this.root.append(button('背景',()=>this.callbacks.background()));
        if(slide){
            const prefix=['slides',this.store.ui.slide];
            for(const[key,label]of[['title','页面标题'],['sub','副标题'],['section','所属小节'],['summary','小结'],['nextUp','下节预告']])this.root.append(field(label,slide[key]||'',v=>write([...prefix,key],v),{live:true,multiline:key==='summary'}));
            this.root.append(button('调整版式',()=>this.callbacks.layout()),button('当前页 JSON',()=>this.callbacks.source('page')),button('AI 改进本页',()=>this.callbacks.ai('page')));
            this.root.append(button('本页转静态 HTML',()=>this.callbacks.html(true)));
        }else this.root.append(button('首页内容与区块',()=>this.callbacks.home()));
        this.root.append(el('p','lde-muted','在画布或元素列表中选择内容，即可修改文字与外观。按住 Shift 多选；双击组合进入内部。'));
    }
}
