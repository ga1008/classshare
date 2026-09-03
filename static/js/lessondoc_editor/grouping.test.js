import {it,expect} from 'vitest';
import {groupSelection,ungroupSelection} from './grouping.js';
import {corners,parentMatrix} from './geometry.js';
const model=()=>({slides:[{id:'s',layout:'canvas',objects:[{id:'a',type:'text',md:'文本',frame:{x:120,y:100,w:280,h:100,r:20},style:{padding:20,size:32,border:{width:3,color:'primary'}}},{id:'b',type:'button',label:'按钮',frame:{x:450,y:160,w:180,h:80,r:-15}}]}]});
it('groups rotated objects without changing their true corners',()=>{
    const d=model(),before=d.slides[0].objects.map(b=>corners(b.frame));groupSelection(d,['a','b']);const g=d.slides[0].objects[0];
    g.children.forEach((b,i)=>corners(b.frame,parentMatrix([g])).forEach((p,j)=>{expect(p.x).toBeCloseTo(before[i][j].x);expect(p.y).toBeCloseTo(before[i][j].y);}));
});
it('ungroups a scaled rotated group preserving corners and all content scale',()=>{
    const d=model();groupSelection(d,['a','b']);const g=d.slides[0].objects[0];g.frame.w*=1.4;g.frame.h*=1.4;g.frame.r=32;
    const before=g.children.map(b=>corners(b.frame,parentMatrix([g])));const ids=ungroupSelection(d,g.id);expect(ids).toEqual(['a','b']);
    d.slides[0].objects.forEach((b,i)=>corners(b.frame).forEach((p,j)=>{expect(p.x).toBeCloseTo(before[i][j].x);expect(p.y).toBeCloseTo(before[i][j].y);}));
    expect(d.slides[0].objects[0].natural).toEqual({w:280,h:100});expect(d.slides[0].objects[0].style.padding).toBe(20);
});
it('refuses unrepresentable shear or referenced groups without changing data',()=>{
    const d=model();groupSelection(d,['a','b']);const g=d.slides[0].objects[0];g.frame.w*=1.3;const before=JSON.stringify(d);
    expect(()=>ungroupSelection(d,g.id)).toThrow('斜切');expect(JSON.stringify(d)).toBe(before);
    g.frame.w/=1.3;g.children[1].actions=[{do:'toggle',target:g.id}];expect(()=>ungroupSelection(d,g.id)).toThrow('动作');
});
