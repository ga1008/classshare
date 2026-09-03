import {clone,locate,rootSelection,at} from './model.js';
import {bounds,corners} from './geometry.js';

export function changeGlobal(model,ids,toGlobal,slideIndex) {
    if(model.kind==='home')throw new Error('首页不使用全局定位元素');
    const items=rootSelection(model,ids);if(items.some(x=>!x.block.frame||x.ancestors.length))throw new Error('请先选择顶层定位元素');
    model.globals??=[];
    if(toGlobal&&model.globals.length+items.filter(x=>x.path[0]!=='globals').length>12)throw new Error('全局元素最多 12 个');
    for(const original of items){const item=locate(model,original.block.id),isGlobal=item.path[0]==='globals';if(isGlobal===toGlobal)continue;
        const parent=at(model,item.path.slice(0,-1)),block=parent.splice(item.path.at(-1),1)[0];
        if(toGlobal){block.skipCovers=true;model.globals.push(block);}else{delete block.skipCovers;delete block.excludeSlides;(model.slides[slideIndex].overlays??=[]).push(block);}
    }
}
export function alignSelection(model,ids,mode) {
    const items=rootSelection(model,ids);if(items.length<2||items.some(x=>!x.block.frame))throw new Error('请选择至少两个定位元素');
    const parent=JSON.stringify(items[0].path.slice(0,-1));if(items.some(x=>JSON.stringify(x.path.slice(0,-1))!==parent))throw new Error('对齐需要同一层的元素');
    const boxes=items.map(x=>bounds(corners(x.block.frame))),outer=bounds(boxes.flatMap(b=>[{x:b.x,y:b.y},{x:b.x+b.w,y:b.y+b.h}]));
    if(mode==='distributeX'||mode==='distributeY'){
        if(items.length<3)throw new Error('分布需要至少三个元素');const axis=mode==='distributeX'?'x':'y',size=axis==='x'?'w':'h';
        const ordered=items.map((item,i)=>({item,box:boxes[i]})).sort((a,b)=>a.box[axis]-b.box[axis]);
        const gap=(outer[size]-ordered.reduce((s,x)=>s+x.box[size],0))/(ordered.length-1);let next=outer[axis];
        for(const{item,box}of ordered){item.block.frame[axis]+=next-box[axis];next+=box[size]+gap;}return;
    }
    items.forEach((item,i)=>{const box=boxes[i],f=item.block.frame;
        if(mode==='left')f.x+=outer.x-box.x;if(mode==='center')f.x+=outer.x+outer.w/2-box.x-box.w/2;if(mode==='right')f.x+=outer.x+outer.w-box.x-box.w;
        if(mode==='top')f.y+=outer.y-box.y;if(mode==='middle')f.y+=outer.y+outer.h/2-box.y-box.h/2;if(mode==='bottom')f.y+=outer.y+outer.h-box.y-box.h;
    });
}

export function flowDestinations(model,slideIndex) {
    if(model.kind==='home')return [];
    const slide=model.slides[slideIndex],base=['slides',slideIndex];
    if(slide.layout==='two-col')return ['left','right'].map((key,index)=>({label:index?'右栏':'左栏',path:[...base,key]}));
    if(slide.layout==='grid')return (slide.areas||[]).map((area,index)=>({label:'区域 '+(index+1),path:[...base,'areas',index,'blocks']}));
    return [];
}

export function moveFlow(model,id,path) {
    const item=locate(model,id);if(!item||item.block.frame)return;
    const source=at(model,item.path.slice(0,-1)),target=at(model,path);
    if(!Array.isArray(source)||!Array.isArray(target)||source===target)return;
    if(path.length>=item.path.length&&item.path.every((key,index)=>path[index]===key))throw new Error('不能将元素移入自身内部。');
    source.splice(item.path.at(-1),1);target.push(item.block);
}
