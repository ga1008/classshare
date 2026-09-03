import {clone,locate} from './model.js';
import {el,button,field,panelSection} from './ui.js';

export function renderTable(root,block,commit,refresh,onError) {
    const cols=Math.max(block.head?.length||0,...(block.rows||[]).map(r=>r.length),1);
    const run=(label,fn)=>{try{commit(label,fn);refresh();}catch(e){onError(e);}};
    const part=panelSection('表格数据');root.append(part.root);
    const head=el('div','lde-table-row');part.body.append(head);
    for(let c=0;c<cols;c++)head.append(field('列 '+(c+1)+' 标题',block.head?.[c]??'',v=>commit('修改表头',b=>{b.head??=Array(cols).fill('');b.head[c]=v;})));
    (block.rows||[]).forEach((row,r)=>{
        const item=panelSection('第 '+(r+1)+' 行',{open:r<4});part.body.append(item.root);
        for(let c=0;c<cols;c++)item.body.append(field('第 '+(r+1)+' 行第 '+(c+1)+' 列',row[c]??'',v=>commit('修改单元格',b=>{while(b.rows[r].length<cols)b.rows[r].push('');b.rows[r][c]=v;})));
        item.body.append(button('删除行',()=>run('删除表格行',b=>{if(b.rows.length<=1)throw new Error('表格至少保留一行。');b.rows.splice(r,1);})));
    });
    part.body.append(button('添加行',()=>run('添加表格行',b=>{if(b.rows.length>=12)throw new Error('一张表格最多 12 行，请拆分表格或页面。');b.rows.push(Array(cols).fill('新内容'));})),button('添加列',()=>run('添加表格列',b=>{b.head??=Array(cols).fill('');while(b.head.length<cols)b.head.push('');b.head.push('新列');b.rows.forEach(row=>{while(row.length<cols)row.push('');row.push('');});})));
    const remove=el('select');remove.setAttribute('aria-label','选择要删除的列');remove.append(new Option('选择要删除的列',''));for(let i=0;i<cols;i++)remove.append(new Option(String(i+1),String(i)));
    remove.addEventListener('change',()=>{if(remove.value==='')return;const c=Number(remove.value);run('删除表格列',b=>{if(cols<=1)throw new Error('表格至少保留一列。');b.head?.splice(c,1);b.rows.forEach(row=>row.splice(c,1));});});part.body.append(remove);
}
export function renderQuiz(root,block,commit,refresh,onError) {
    root.append(field('题干',block.q||block.question||'',v=>commit('修改题干',b=>b.q=v),{multiline:true}));
    const choices={'':'请选择正确答案'};
    block.options.forEach((option,i)=>{
        const key=option.k||option.key||String.fromCharCode(65+i);choices[key]=key+' · '+(option.text||'').slice(0,30);
        const row=el('div','lde-array-item');root.append(row);row.append(field('选项 '+key,option.text||'',v=>commit('修改选项',b=>b.options[i].text=v),{multiline:true}),button('删除选项',()=>{
            try{commit('删除选项',b=>{if(b.options.length<=2)throw new Error('测验至少保留两个选项');const removed=b.options.splice(i,1)[0];if(b.answer===(removed.k||removed.key))b.answer='';});refresh();}catch(e){onError(e);}
        }));
    });
    root.append(button('添加选项',()=>{try{commit('添加选项',b=>{if(b.options.length>=6)throw new Error('最多 6 个选项');const used=new Set(b.options.map(o=>o.k||o.key));const k=['A','B','C','D','E','F'].find(k=>!used.has(k));b.options.push({k,text:'新选项'});});refresh();}catch(e){onError(e);}}));
    root.append(field('正确答案',block.answer||'',v=>commit('设置正确答案',b=>b.answer=v),{choices}),field('解析',block.explain||block.exp||'',v=>commit('修改解析',b=>b.explain=v),{multiline:true}));
    if(!block.answer)root.append(el('p','lde-dialog-error','请选择正确答案后再保存。'));
}
export function moveCodewalkLine(block,from,to) {
    if(to<0||to>=block.lines.length)return;
    const sources=new Map();let count=0;block.lines.forEach((line,index)=>{if(typeof line==='string'||line.code!=null)sources.set(count++,index);});
    const wrapped=block.lines.map((line,index)=>({line,index}));const [moved]=wrapped.splice(from,1);wrapped.splice(to,0,moved);
    const indexes=new Map();count=0;
    const next=wrapped.map(({line,index})=>{if(typeof line==='string'||line.code!=null){indexes.set(index,count++);return line;}const source=sources.get(line.ref);if(!indexes.has(source))throw new Error('移动会让引用先于源码出现，请先调整执行轨迹。');return {...line,ref:indexes.get(source)};});
    block.lines=next;
}
export function renderCodewalk(root,block,commit,refresh,onError) {
    for(const[key,label]of[['title','演示标题'],['lang','语言'],['runLabel','运行按钮文字']])root.append(field(label,block[key]||'',v=>commit('修改代码演示',b=>b[key]=v)));
    root.append(field('每步毫秒',block.speedMs??900,v=>commit('调整速度',b=>b.speedMs=v),{type:'number',min:200,max:5000}));
    for(const[key,label]of[['loop','循环'],['autoStart','自动播放'],['arrow','显示箭头'],['showOutput','显示输出'],['showNotes','显示注释']])root.append(field(label,block[key]??!['loop','autoStart'].includes(key),v=>commit('修改演示选项',b=>b[key]=v),{type:'checkbox'}));
    let sourceCount=0;
    block.lines.forEach((raw,index)=>{
        const line=typeof raw==='string'?{code:raw}:raw,ref=line.code==null&&line.ref!=null,label=ref?'执行轨迹 → 第 '+(line.ref+1)+' 行':'源码第 '+(++sourceCount)+' 行';
        const part=panelSection(label,{open:index<4});root.append(part.root);
        const update=(key,value)=>commit('编辑代码轨迹',b=>{if(typeof b.lines[index]==='string')b.lines[index]={code:b.lines[index]};b.lines[index][key]=value;});
        if(ref)part.body.append(field('引用源码行',line.ref+1,v=>update('ref',v-1),{type:'number',min:1,max:sourceCount}));
        else part.body.append(field('源码',line.code||'',v=>update('code',v),{multiline:true,rows:2}));
        part.body.append(field('本步输出',line.out||'',v=>update('out',v),{multiline:true,rows:2}),field('本步注释',line.note||'',v=>update('note',v),{multiline:true,rows:2}));
        for(const[direction,title]of[[-1,'上移'],[1,'下移']])part.body.append(button(title,()=>{try{commit('移动执行轨迹',b=>moveCodewalkLine(b,index,index+direction));refresh();}catch(e){onError(e);}}));
        part.body.append(button('删除这步',()=>{try{commit('删除执行轨迹',b=>{let source=b.lines.slice(0,index).filter(x=>typeof x==='string'||x.code!=null).length;const removed=b.lines.splice(index,1)[0];if(typeof removed==='string'||removed.code!=null){for(let i=b.lines.length-1;i>=0;i--){const next=b.lines[i];if(next.code==null&&next.ref===source)b.lines.splice(i,1);else if(next.code==null&&next.ref>source)next.ref--;}}if(!b.lines.some(x=>typeof x==='string'||x.code!=null))throw new Error('至少保留一行源码');});refresh();}catch(e){onError(e);}}));
    });
    for(const[ref,title]of[[false,'添加源码行'],[true,'再次执行某行']])root.append(button(title,()=>{try{commit('添加执行轨迹',b=>{if(b.lines.length>=60)throw new Error('最多 60 步');b.lines.push(ref?{ref:0,note:'重复执行'}:{code:'新代码'});});refresh();}catch(e){onError(e);}}));
}

export const DIAGRAM_DEFAULTS={
    flow:{nodes:[{id:'a',label:'开始'},{id:'b',label:'完成'}],edges:[{from:'a',to:'b'}]},
    sequence:{actors:[{id:'client',label:'客户端'},{id:'server',label:'服务端'}],messages:[{from:'client',to:'server',label:'请求'},{from:'server',to:'client',label:'响应'}]},
    arch:{layers:[{label:'应用层',nodes:[{id:'app',label:'应用服务'}]},{label:'数据层',nodes:[{id:'data',label:'数据存储'}]}],links:[{from:'app',to:'data'}]},
    mindmap:{root:'核心概念',children:[{label:'概念一',children:[{label:'具体示例'}]},{label:'概念二'}]},
};
