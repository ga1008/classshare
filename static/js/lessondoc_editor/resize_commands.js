import {clone,locate} from './model.js';

export const sizeFrame=block=>block.frame||block.flowFrame;
// Templates travel in a positioned envelope; preserve their size in flow layouts.
export function adaptSizing(block,positioned) {
    if(positioned){
        if(block.flowFrame){block.frame={...block.flowFrame,x:80,y:100};delete block.flowFrame;}
    }else if(block.frame&&block.type!=='group'){
        if(block.natural)block.flowFrame={...block.frame,x:0,y:0};
        delete block.frame;
    }
    return block;
}
export function enableResizing(model,id,measured) {
    const item=locate(model,id);if(!item)throw new Error('元素已不存在。');
    if(item.ancestors.some(b=>b.type!=='group'))throw new Error('请先选中外层容器并转换，容器内的内容会一起缩放。');
    const block=item.block;if(block.type==='group'||(sizeFrame(block)&&block.natural))return;
    const frame=sizeFrame(block)||measured;
    if(!frame||!Number.isFinite(frame.w)||!Number.isFinite(frame.h)||frame.w<1||frame.h<1)throw new Error('元素尚未显示，请先展开内容再转换。');
    if(frame.w>10000||frame.h>10000)throw new Error('此元素内容过长，请先拆分内容再转换。');
    block.natural={w:Math.max(8,frame.w),h:Math.max(8,frame.h)};
    const scale=Math.min(1,1680/block.natural.w,1680/block.natural.h);
    if(!block.frame)block.flowFrame={x:0,y:0,w:Math.max(8,Math.min(1680,block.natural.w*scale)),h:Math.max(8,Math.min(1680,block.natural.h*scale))};
}
export function commitFrames(model,items,frames) {
    for(const original of items){
        const next=frames.get(original.block.id),item=locate(model,original.block.id);if(!next||!item)continue;
        const key=item.block.frame?'frame':'flowFrame';item.block[key]=clone(next);
        if(original.natural&&!item.block.natural)item.block.natural=clone(original.natural);
    }
}
