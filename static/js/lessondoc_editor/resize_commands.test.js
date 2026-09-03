import {it,expect} from 'vitest';
import {RESIZE_HANDLES,resizedFrame,frameMatrix,parentMatrix,multiply,transform,resizeCursor} from './geometry.js';
import {enableResizing,commitFrames,adaptSizing} from './resize_commands.js';
import {EditorStore} from './state.js';
import {locate} from './model.js';
import {convertLayout} from './layout_conversion.js';
import {REGISTRY} from './registry.js';
import {CanvasController} from './canvas_controller.js';

const model=block=>({kind:'lesson',lesson:1,slides:[{id:'s',layout:'content',blocks:[block]}]});
const point=(frame,x,y,parents=[])=>transform(multiply(parentMatrix(parents),frameMatrix(frame)),{x:x*frame.w,y:y*frame.h});
for(const [direction,[x,y]]of Object.entries(RESIZE_HANDLES))for(const rotation of [0,38,-117]){
    it(`resizes ${direction} at ${rotation} degrees with a fixed opposite anchor under a scaled rotated group`,()=>{
        const parents=[{type:'group',frame:{x:120,y:40,w:720,h:400,r:19},natural:{w:480,h:400}}];
        const f={x:100,y:80,w:240,h:120,r:rotation},corner=direction.length===2;
        const ax=x===.5?.5:1-x,ay=y===.5?.5:1-y;
        const target=point(f,x===.5?x:ax+(x-ax)*1.4,y===.5?y:ay+(y-ay)*1.4,parents);
        const next=resizedFrame(f,target,parents,{handle:direction,uniform:corner});
        const oldAnchor=point(f,ax,ay,parents),newAnchor=point(next,ax,ay,parents);
        expect(newAnchor.x).toBeCloseTo(oldAnchor.x,6);expect(newAnchor.y).toBeCloseTo(oldAnchor.y,6);
        expect(next.w).toBeCloseTo(f.w*(x===.5?1:1.4));expect(next.h).toBeCloseTo(f.h*(y===.5?1:1.4));
        if(corner)expect(next.w/next.h).toBeCloseTo(f.w/f.h);
        else expect(next.w/next.h).not.toBeCloseTo(f.w/f.h);
    });
}
it('clamps crossing and oversized gestures without flipping or losing the anchor',()=>{
    const f={x:10,y:20,w:240,h:120,r:12};
    for(const [handle,[x,y]]of Object.entries(RESIZE_HANDLES))for(const factor of [-8,100]){
        const ax=x===.5?.5:1-x,ay=y===.5?.5:1-y,next=resizedFrame(f,point(f,ax+(x-ax)*factor,ay+(y-ay)*factor),[],{handle,uniform:handle.length===2});
        expect(next.w).toBeGreaterThanOrEqual(8-1e-6);expect(next.h).toBeGreaterThanOrEqual(8-1e-6);
        expect(next.w).toBeLessThanOrEqual(1680);expect(next.h).toBeLessThanOrEqual(1680);
        expect(point(next,ax,ay).x).toBeCloseTo(point(f,ax,ay).x);expect(point(next,ax,ay).y).toBeCloseTo(point(f,ax,ay).y);
    }
});
it('rotates resize cursors with the element',()=>{
    expect(resizeCursor('e')).toBe('ew-resize');expect(resizeCursor('n')).toBe('ns-resize');
    expect(resizeCursor('se')).toBe('nwse-resize');expect(resizeCursor('sw')).toBe('nesw-resize');
    expect(resizeCursor('e',[0,1,-1,0,0,0])).toBe('ns-resize');
});
it('keeps every native block type, content, IDs and actions through sizing conversion',()=>{
    for(const {type,defaults}of Object.values(REGISTRY).filter(e=>e.type!=='group')){
        const block={...structuredClone(defaults),id:'b',type,actions:[{do:'toggle',target:'b'}]},d=model(block),original=structuredClone(block);
        enableResizing(d,'b',{w:600,h:300});expect(block).toMatchObject(original);
        expect(block.flowFrame).toEqual({x:0,y:0,w:600,h:300});expect(block.natural).toEqual({w:600,h:300});
        enableResizing(d,'b',{w:200,h:100});expect(block.natural).toEqual({w:600,h:300});
    }
});
it('keeps conversion and resizing reversible and preserves the first intrinsic basis on repeat resize',()=>{
    const s=new EditorStore(model({type:'text',id:'b',md:'editable'}),'r0');
    s.command('convert',d=>enableResizing(d,'b',{w:600,h:120}));
    const item=locate(s.model,'b');s.command('resize',d=>commitFrames(d,[item],new Map([['b',{x:0,y:0,w:300,h:60}]])));
    expect(locate(s.model,'b').block.natural).toEqual({w:600,h:120});
    s.undo();expect(locate(s.model,'b').block.flowFrame.w).toBe(600);s.undo();expect(locate(s.model,'b').block.flowFrame).toBeUndefined();
    s.redo();s.redo();expect(locate(s.model,'b').block.flowFrame.w).toBe(300);
});
it('preserves scaled content across flow, canvas and reusable positioned envelopes',()=>{
    const b={type:'text',id:'b',md:'hello',flowFrame:{x:12,y:8,w:300,h:60,r:20},natural:{w:600,h:120}};
    const canvas=convertLayout(model(b).slides[0],'canvas',{b:{x:40,y:70,w:300,h:60,r:20}});
    expect(canvas.objects[0].flowFrame).toBeUndefined();expect(canvas.objects[0].frame.r).toBe(20);
    const flow=convertLayout(canvas,'content').blocks[0];expect(flow.flowFrame).toMatchObject({w:300,h:60,r:20});
    expect(adaptSizing(adaptSizing(structuredClone(b),true),false)).toMatchObject({natural:b.natural,flowFrame:{w:300,h:60,r:20}});
});
it('rejects unavailable and nested flow conversion without partial mutation, fits long content',()=>{
    const b={type:'text',id:'b',md:'hello'},d=model(b);expect(()=>enableResizing(d,'b',null)).toThrow();expect(b.natural).toBeUndefined();
    expect(()=>enableResizing(model({type:'details',id:'outer',blocks:[b]}),'b',{w:100,h:30})).toThrow('外层容器');
    enableResizing(d,'b',{w:600,h:3000});expect(b.flowFrame.h).toBe(1680);expect(b.flowFrame.w/b.flowFrame.h).toBeCloseTo(.2);
});
it('flushes the final pointer position into one undo command and cancels stale model gestures',()=>{
    const s=new EditorStore(model({id:'b',type:'text',md:'hello',frame:{x:100,y:100,w:200,h:100}}),'r0');
    const c=Object.create(CanvasController.prototype);c.store=s;c.previewFrame=()=>{};c.draw=()=>{};c.release=()=>{};c.onError=e=>{throw e;};
    let restored=0;c.bridge={point:e=>({x:e.clientX,y:e.clientY}),api:{geometry:()=>({scale:.5})},render:()=>restored++};
    const gesture=()=>({model:s.model,pointerId:1,mode:'resize',handle:'se',start:{x:300,y:200},handleStart:{x:300,y:200},frames:new Map(),selected:[{...locate(s.model,'b'),natural:{w:200,h:100}}],active:false});
    const event={pointerId:1,clientX:340,clientY:220,preventDefault(){}};c.gesture=gesture();c.up(event);
    expect(locate(s.model,'b').block.frame).toMatchObject({w:240,h:120});expect(s.undoStack).toHaveLength(1);
    expect(locate(s.model,'b').block.natural).toEqual({w:200,h:100});s.undo();expect(locate(s.model,'b').block.natural).toBeUndefined();
    c.gesture=gesture();s.command('other',d=>d.title='changed');c.up(event);expect(restored).toBe(1);expect(locate(s.model,'b').block.frame.w).toBe(200);
});
