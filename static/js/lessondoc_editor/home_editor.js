import {clone,uid} from './model.js';
import {mergeCanonical} from './state.js';
import {dialog,el,button,field,panelSection} from './ui.js';
import {gradientField} from './appearance_controls.js';
import {assignStageLesson} from './home_controls.js';
const labels={hero:'课程抬头',mindmap:'课程导图',nav:'课次导航',blocks:'课程说明',tabs:'补充资料',footer:'页脚'};
export function openHomeEditor(store,api,onError,config={}) {
    const before=clone(store.model),serial=store.serial,candidate=clone(before);candidate.home??={};candidate.home.sections??=Object.keys(labels).map(key=>({key}));candidate.home.style??={};candidate.tabs??=[];
    candidate.stages??=[];let active=true;
    dialog('课程首页',({body,foot,close})=>{
        body.append(el('p','lde-muted','这里修改首页展示信息。课次发布状态和课堂绑定由系统管理。'));
        for(const[key,label]of[['name','展示名称'],['teacher','展示教师'],['department','开课单位'],['totalHours','总学时'],['credits','学分'],['assessment','考核方式']])body.append(field(label,candidate.course[key]??'',v=>candidate.course[key]=v));
        body.append(field('课程简介',candidate.course.intro||'',v=>candidate.course.intro=v,{multiline:true}));
        const stages=panelSection('课次分组',{open:false});body.append(stages.root);
        const lessons=new Map((config.lessons||[]).map(item=>[item.n,item.title]));
        for(const stage of candidate.stages)for(const n of stage.lessons||[])if(!lessons.has(n))lessons.set(n,'第 '+n+' 课');
        const summaries=new Map();
        const drawStages=()=>{stages.body.replaceChildren();summaries.clear();candidate.stages.forEach((stage,index)=>{
            const row=el('div','lde-array-item'),pick=panelSection('选择课次 · 已选 '+(stage.lessons?.length||0)+' 课',{open:false});pick.root.name='lde-stage-lessons';
            row.append(field('分组名称',stage.label,v=>stage.label=v),pick.root,button('移除分组',()=>{candidate.stages.splice(index,1);drawStages();}));
            summaries.set(stage,pick.root.querySelector('summary'));
            pick.root.addEventListener('toggle',()=>{if(!pick.root.open)return;pick.body.replaceChildren();pick.body.classList.add('lde-lesson-choices');for(const [n,title]of lessons){
                const owner=candidate.stages.find(other=>other!==stage&&other.lessons?.includes(n));
                const b=button(n+' · '+title,()=>{const selected=!stage.lessons?.includes(n);assignStageLesson(candidate.stages,index,n,selected);b.setAttribute('aria-pressed',String(selected));b.querySelector('small')?.remove();for(const [item,summary]of summaries)summary.textContent='选择课次 · 已选 '+item.lessons.length+' 课';},'lde-choice');
                b.setAttribute('aria-pressed',String(stage.lessons?.includes(n)||false));if(owner)b.append(el('small','',owner.label+' → 移入本组'));pick.body.append(b);
            }});
            stages.body.append(row);
        });stages.body.append(button('添加分组',()=>{if(candidate.stages.length>=200)return;candidate.stages.push({label:'新分组',lessons:[]});drawStages();}),el('p','lde-muted','未分组的课次自动归入其他课次。'));};drawStages();
        const sections=el('div');body.append(sections);
        const renderSections=()=>{sections.replaceChildren();candidate.home.sections.forEach((section,index)=>{
            const part=panelSection(labels[section.key]);sections.append(part.root);
            part.body.append(field('显示本区块',!section.hidden,v=>section.hidden=!v,{type:'checkbox'}),field('区块标题',section.title||'',v=>section.title=v));
            if(section.key==='hero')for(const [k,title]of[['totalHours','学时'],['sessionCount','课次数'],['credits','学分'],['assessment','考核方式']])part.body.append(field('显示'+title,(section.stats||['totalHours','sessionCount','credits','assessment']).includes(k),v=>{const values=new Set(section.stats||['totalHours','sessionCount','credits','assessment']);if(v)values.add(k);else values.delete(k);section.stats=[...values];},{type:'checkbox'}));
            if(section.key==='mindmap')part.body.append(field('初始展开深度',section.collapsedDepth??1,v=>section.collapsedDepth=v,{type:'number',min:0,max:3}));
            part.body.append(button('上移区块',()=>{if(index>0)[candidate.home.sections[index-1],candidate.home.sections[index]]=[section,candidate.home.sections[index-1]];renderSections();}));
        });};renderSections();
        body.append(field('课次卡片圆角',candidate.home.style.cardRadius??18,v=>candidate.home.style.cardRadius=v,{type:'number',min:0,max:120}));
        const appearance=panelSection('首页外观',{open:false});body.append(appearance.root);
        for(const[key,label,type,min,max]of[['color','正文颜色','color'],['size','正文字号','number',12,160],['bg','内容背景','color'],['padding','内容边距','number',0,120]])appearance.body.append(field(label,candidate.home.style[key],v=>candidate.home.style[key]=v,{type,min,max,unit:' px'}));
        appearance.body.append(gradientField('抬头渐变',candidate.home.style.heroGradient,v=>candidate.home.style.heroGradient=v));
        const tabs=panelSection('补充资料标签页');body.append(tabs.root);
        const drawTabs=()=>{tabs.body.replaceChildren();candidate.tabs.forEach((tab,index)=>{const row=el('div','lde-list-row');row.append(field('标签 '+(index+1),tab.label||'',v=>tab.label=v),button('上移',()=>{if(index>0)[candidate.tabs[index-1],candidate.tabs[index]]=[tab,candidate.tabs[index-1]];drawTabs();}),button('下移',()=>{if(index<candidate.tabs.length-1)[candidate.tabs[index+1],candidate.tabs[index]]=[tab,candidate.tabs[index+1]];drawTabs();}),button('移除',()=>{candidate.tabs.splice(index,1);drawTabs();}));tabs.body.append(row);});tabs.body.append(button('添加标签页',()=>{candidate.tabs.push({label:'新标签',blocks:[{id:uid(),type:'text',md:'补充说明'}]});drawTabs();}));};drawTabs();
        foot.append(button('取消',close),button('应用首页设置',async()=>{try{
            if(serial!==store.serial)throw new Error('打开设置后正文已变化，请重新打开。');
            if(candidate.stages.some(stage=>!stage.label?.trim()||!stage.lessons?.length))throw new Error('请为每个分组填写名称并选择至少一课，或移除空分组。');
            const snapshot=JSON.stringify(candidate);
            const checked=await api.validate(mergeCanonical(before,store.model,candidate));if(!checked.valid)throw new Error(checked.warnings.join('\n'));
            if(!active)return;
            if(serial!==store.serial||snapshot!==JSON.stringify(candidate))throw new Error('预检期间内容已变化，请再次应用。');
            store.command('编辑首页',model=>{for(const key of ['course','home','tabs','stages'])model[key]=checked.document[key];});close();
        }catch(error){onError(error);}},'lde-button lde-primary'));
    },{onClose:()=>active=false});
}
