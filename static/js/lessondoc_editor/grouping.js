import {clone,locate,rootSelection,at,uid,walkBlocks} from './model.js';
import {bounds,corners,frameMatrix,multiply,transform} from './geometry.js';

function depth(block) {return block.type==='group'?1+Math.max(0,...(block.children||[]).map(depth)):0;}
function validFrame(frame) {
    return frame.x>=-200&&frame.x<=1480&&frame.y>=-200&&frame.y<=920&&frame.w>=8&&frame.w<=1680&&frame.h>=8&&frame.h<=1680;
}
export function groupSelection(model,ids) {
    const items=rootSelection(model,ids);if(items.length<2)throw new Error('请选择至少两个同层定位元素。');
    const key=JSON.stringify(items[0].path.slice(0,-1));
    if(items.some(item=>!item.block.frame||JSON.stringify(item.path.slice(0,-1))!==key))throw new Error('组合需要位于同一层的定位元素。流式内容可先转换为自由画布。');
    const ancestors=items[0].ancestors.filter(b=>b.type==='group').length;
    if(ancestors+1+Math.max(...items.map(item=>depth(item.block)))>2)throw new Error('组合最多嵌套两层，请先整理现有组合。');
    const box=bounds(items.flatMap(item=>corners(item.block.frame)));
    if(!validFrame(box))throw new Error('组合范围超过画布允许范围，请将元素移近或缩小后再组合。');
    const parent=at(model,items[0].path.slice(0,-1)),chosen=new Set(items.map(item=>item.block.id));
    const ordered=[...parent].sort((a,b)=>(a.frame?.z||0)-(b.frame?.z||0)),first=ordered.findIndex(b=>chosen.has(b.id));
    const children=ordered.filter(b=>chosen.has(b.id)).map(b=>({...clone(b),frame:{...b.frame,x:b.frame.x-box.x,y:b.frame.y-box.y}}));
    const group={type:'group',id:uid('g'),name:'组合',frame:{...box,z:items[0].block.frame.z||0},natural:{w:box.w,h:box.h},children};
    const next=ordered.filter(b=>!chosen.has(b.id));next.splice(first,0,group);next.forEach((b,i)=>{b.frame.z=i;});parent.splice(0,parent.length,...next);return group.id;
}
export function ungroupSelection(model,id) {
    const item=locate(model,id),group=item?.block;if(group?.type!=='group'||!group.frame)throw new Error('请选择一个定位组合。');
    if(group.hidden||group.actions?.length||Object.keys(group.style||{}).length)throw new Error('此组合有整体样式、显隐或动作。请先清除这些整体属性再拆组，以免改变显示或交互。');
    let referenced=false;walkBlocks(model,b=>{if(b.actions?.some(a=>a.target===id))referenced=true;});
    if(referenced)throw new Error('有动作指向这个组合，请先调整动作目标再拆组。');
    const parent=at(model,item.path.slice(0,-1));if(!Array.isArray(parent))throw new Error('这个组合不能从当前容器拆出。');
    const f=group.frame,n=group.natural||f,m=multiply(frameMatrix(f),[f.w/n.w,0,0,f.h/n.h,0,0]);
    const children=group.children.map(child=>{
        const points=corners(child.frame,m),vx={x:points[1].x-points[0].x,y:points[1].y-points[0].y},vy={x:points[3].x-points[0].x,y:points[3].y-points[0].y};
        const w=Math.hypot(vx.x,vx.y),h=Math.hypot(vy.x,vy.y);
        if(Math.abs(vx.x*vy.x+vx.y*vy.y)>w*h*1e-6)throw new Error('非等比缩放与子元素旋转产生了斜切，当前模型无法无损拆组。请先恢复组合的等比尺寸。');
        const center={x:(points[0].x+points[2].x)/2,y:(points[0].y+points[2].y)/2};
        const frame={x:center.x-w/2,y:center.y-h/2,w,h,r:Math.atan2(vx.y,vx.x)*180/Math.PI,z:child.frame.z||0};
        if(!validFrame(frame))throw new Error('拆分后元素超出允许范围，请先调整组合大小与位置。');
        const result={...clone(child),frame};
        if(child.type!=='group'&&!child.natural)result.natural={w:child.frame.w,h:child.frame.h};
        for(const key of ['skipCovers','excludeSlides'])if(group[key]!==undefined)result[key]=clone(group[key]);
        return result;
    });
    const ordered=[...parent].sort((a,b)=>(a.frame?.z||0)-(b.frame?.z||0)),index=ordered.findIndex(b=>b.id===id);
    ordered.splice(index,1,...children.sort((a,b)=>(a.frame.z||0)-(b.frame.z||0)));ordered.forEach((b,i)=>{if(b.frame)b.frame.z=i;});parent.splice(0,parent.length,...ordered);return children.map(b=>b.id);
}
