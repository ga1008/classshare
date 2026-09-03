import {clone,uid} from './model.js';
import {mergeCanonical} from './state.js';
import {parseStagesText,stagesToText} from '../lessondoc_stages.js';
import {dialog,el,button,field,panelSection} from './ui.js';
const labels={hero:'课程抬头',mindmap:'课程导图',nav:'课次导航',blocks:'课程说明',tabs:'补充资料',footer:'页脚'};
export function openHomeEditor(store,api,onError) {
    const before=clone(store.model),serial=store.serial,candidate=clone(before);candidate.home??={};candidate.home.sections??=Object.keys(labels).map(key=>({key}));candidate.home.style??={};candidate.tabs??=[];
    let stagesText=stagesToText(candidate.stages);
    dialog('课程首页',({body,foot,close})=>{
        body.append(el('p','lde-muted','这里修改首页展示信息。课次发布状态和课堂绑定由系统管理。'));
        for(const[key,label]of[['name','展示名称'],['teacher','展示教师'],['department','开课单位'],['totalHours','总学时'],['credits','学分'],['assessment','考核方式']])body.append(field(label,candidate.course[key]??'',v=>candidate.course[key]=v));
        body.append(field('课程简介',candidate.course.intro||'',v=>candidate.course.intro=v,{multiline:true}),field('阶段分组',stagesText,v=>stagesText=v,{multiline:true,rows:4}),el('p','lde-muted','每行使用“阶段名: 1-4, 6”。未分组的课次自动归入其他课次。'));
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
        for(const[key,label,type]of[['color','正文颜色','text'],['size','正文字号','number'],['bg','内容背景','text'],['padding','内容边距','number']])appearance.body.append(field(label,candidate.home.style[key]??'',v=>candidate.home.style[key]=v,{type}));
        const gradient=candidate.home.style.heroGradient||{};
        for(const[key,label]of[['from','抬头渐变起色'],['to','抬头渐变终色'],['angle','渐变角度']])appearance.body.append(field(label,gradient[key]??'',v=>{candidate.home.style.heroGradient??={...gradient};candidate.home.style.heroGradient[key]=v;},{type:key==='angle'?'number':'text'}));
        const tabs=panelSection('补充资料标签页');body.append(tabs.root);
        const drawTabs=()=>{tabs.body.replaceChildren();candidate.tabs.forEach((tab,index)=>{const row=el('div','lde-list-row');row.append(field('标签 '+(index+1),tab.label||'',v=>tab.label=v),button('上移',()=>{if(index>0)[candidate.tabs[index-1],candidate.tabs[index]]=[tab,candidate.tabs[index-1]];drawTabs();}),button('下移',()=>{if(index<candidate.tabs.length-1)[candidate.tabs[index+1],candidate.tabs[index]]=[tab,candidate.tabs[index+1]];drawTabs();}),button('移除',()=>{candidate.tabs.splice(index,1);drawTabs();}));tabs.body.append(row);});tabs.body.append(button('添加标签页',()=>{candidate.tabs.push({label:'新标签',blocks:[{id:uid(),type:'text',md:'补充说明'}]});drawTabs();}));};drawTabs();
        foot.append(button('取消',close),button('应用首页设置',async()=>{try{
            if(serial!==store.serial)throw new Error('打开设置后正文已变化，请重新打开。');
            candidate.stages=parseStagesText(stagesText);
            const checked=await api.validate(mergeCanonical(before,store.model,candidate));if(!checked.valid)throw new Error(checked.warnings.join('\n'));
            if(serial!==store.serial)throw new Error('预检期间正文已变化，请重新打开。');
            store.command('编辑首页',model=>{for(const key of ['course','home','tabs','stages'])model[key]=checked.document[key];});close();
        }catch(error){onError(error);}},'lde-button lde-primary'));
    });
}
