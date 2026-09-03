import {it,expect} from 'vitest';
import {movedSelection,offsetPastedElements,resizedFrame,corners,bounds} from './geometry.js';
import {moveFlow,flowDestinations} from './object_commands.js';

it('moves a multi-selection as one gesture at a legal boundary',()=>{
    const a={id:'a',frame:{x:1460,y:50,w:20,h:20}},b={id:'b',frame:{x:1400,y:80,w:20,h:20}};
    const moved=movedSelection([{block:a},{block:b}],{x:100,y:40});
    expect(moved.get('a').x).toBe(1480);expect(moved.get('a').x-moved.get('b').x).toBe(60);
    expect(moved.get('a').y).toBe(58);expect(a.frame.x).toBe(1460);
});
it('pastes rotated objects within the canvas without changing spacing',()=>{
    const elements=[{id:'a',frame:{x:1000,y:600,w:150,h:100,r:20}},{id:'b',frame:{x:1200,y:650,w:50,h:50,r:-10}}];
    offsetPastedElements(elements);const box=bounds(elements.flatMap(b=>corners(b.frame)));
    expect(box.x+box.w).toBeLessThanOrEqual(1280.001);expect(box.y+box.h).toBeLessThanOrEqual(720.001);
    expect(elements[1].frame.x-elements[0].frame.x).toBe(200);
});
it('caps uniform scaling without changing the aspect ratio or fixed corner',()=>{
    const f={x:100,y:80,w:800,h:200,r:25},original=corners(f)[0];
    const resized=resizedFrame(f,{x:6000,y:6000},[],{uniform:true});
    expect(resized.w).toBeLessThanOrEqual(1680);expect(resized.w/resized.h).toBe(4);
    expect(corners(resized)[0].x).toBeCloseTo(original.x);expect(corners(resized)[0].y).toBeCloseTo(original.y);
});
it('moves flow content into an empty column preserving its identity and actions',()=>{
    const block={id:'a',type:'text',md:'正文',actions:[{do:'toggle',target:'a'}]},model={slides:[{layout:'two-col',left:[block],right:[]}]};
    moveFlow(model,'a',flowDestinations(model,0)[1].path);
    expect(model.slides[0].left).toEqual([]);expect(model.slides[0].right[0]).toBe(block);
});
