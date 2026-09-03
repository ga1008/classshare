import {clone,locate,walkBlocks} from './model.js';
import {REGISTRY} from './registry.js';
import {dialog,el,button,field} from './ui.js';
const kinds={show:'显示',hide:'隐藏',toggle:'切换显隐',move:'相对移动',moveTo:'移动到',goto:'跳转页面',next:'下一页',prev:'上一页',run:'运行代码',reset:'重置代码'};
export function openActions(store,api,onError) {
    const id=store.ui.selection[0],block=locate(store.model,id)?.block;if(!block)return;
    let steps=clone(block.actions||[]),once=!!block.once;
    dialog('点击动作',({body,foot,close})=>{
        const list=el('div');body.append(el('p','lde-muted','按顺序执行。试播只改变预览状态，退出后恢复正文中的位置与显隐。'),list);
        const render=()=>{
            list.replaceChildren();steps.forEach((step,index)=>{
                const row=el('div','lde-array-item');list.append(row);
                row.append(field('第 '+(index+1)+' 步',step.do,value=>{steps[index]={do:value};render();},{choices:store.model.kind==='home'?Object.fromEntries(Object.entries(kinds).filter(([k])=>!['goto','next','prev'].includes(k))):kinds}));
                if(!['goto','next','prev'].includes(step.do)){
                    const targets={};const scope=store.model.kind==='home'?store.model:store.model.slides[store.ui.slide];
                    const collect=b=>{if(['run','reset'].includes(step.do)&&b.type!=='codewalk')return;if(['move','moveTo'].includes(step.do)&&!b.frame)return;targets[b.id]=(b.name||REGISTRY[b.type]?.label||b.type)+' · '+(b.md||b.label||b.title||b.id).slice(0,25);};
                    walkBlocks(scope,collect);walkBlocks({globals:store.model.globals},collect);
                    row.append(field('目标元素',step.target||'',value=>step.target=value,{choices:{'':'请选择目标',...targets}}));
                }
                if(step.do==='goto')row.append(field('目标页面',step.slideId||store.model.slides[(step.slide||1)-1]?.id||'',value=>{step.slideId=value;delete step.slide;},{choices:Object.fromEntries([['','请选择页面'],...store.model.slides.map((s,i)=>[s.id,(i+1)+' · '+(s.title||'未命名')])])}));
                for(const key of step.do==='move'?['dx','dy']:step.do==='moveTo'?['x','y']:[])row.append(field(key==='dx'||key==='x'?'水平方向':'垂直方向',step[key]||0,value=>step[key]=value,{type:'number',min:-2000,max:2000}));
                if(['move','moveTo','show','hide'].includes(step.do)){row.append(field('时长（毫秒）',step.ms??400,value=>step.ms=value,{type:'number',min:0,max:5000}),field('速度曲线',step.ease||'inout',value=>step.ease=value,{choices:{linear:'匀速',in:'渐入',out:'渐出',inout:'平滑'}}));}
                row.append(button('上移',()=>{if(index>0)[steps[index-1],steps[index]]=[steps[index],steps[index-1]];render();}),button('删除这步',()=>{steps.splice(index,1);render();}));
            });
            list.append(button('添加动作',()=>{if(steps.length>=12)return;steps.push({do:'toggle'});render();}));
        };render();body.append(field('每次进入页面只执行一次',once,v=>once=v,{type:'checkbox'}));
        foot.append(button('取消',close),button('应用动作',async()=>{
            try{const candidate=clone(store.model),target=locate(candidate,id).block;target.actions=steps;target.once=once;
                const checked=await api.validate(candidate);if(!checked.valid||(locate(checked.document,id)?.block.actions||[]).length!==steps.length)throw new Error(checked.warnings.join('\n')||'请为每一步选择有效目标');
                store.command('修改动作',model=>{const item=locate(model,id).block;item.actions=clone(steps);item.once=once;});close();
            }catch(error){onError(error);}
        },'lde-button lde-primary'));
    });
}
