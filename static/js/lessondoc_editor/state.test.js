import {describe,it,expect} from 'vitest';
import {EditorStore} from './state.js';
import {locate,freshInstance,removeBlocks} from './model.js';
import {parentMatrix,inverse,transform,corners,resizedFrame,movedFrame} from './geometry.js';

const document=()=>({spec:'lessondoc/2.0',kind:'lesson',lesson:1,slides:[{id:'s1',layout:'content',blocks:[{id:'b1',type:'text',md:'开始'}]}]});
describe('editor save and command boundaries',()=>{
    it('keeps typing and canonical values when a save finishes late, with usable undo',()=>{
        const s=new EditorStore(document(),'r0');
        s.command('写字',d=>d.slides[0].blocks[0].md='提交'); const attempt=s.beginSave();
        s.command('继续写字',d=>d.slides[0].blocks[0].md='继续');
        const clean=structuredClone(attempt.document);clean.slides[0].blocks[0].name='规范名称';
        s.savedResponse({document:clean,revision:'r1'});
        expect(s.model.slides[0].blocks[0]).toMatchObject({md:'继续',name:'规范名称'});expect(s.dirty).toBe(true);
        s.undo();expect(s.dirty).toBe(false);s.undo();expect(s.model.slides[0].blocks[0].md).toBe('开始');s.redo();expect(s.dirty).toBe(false);
    });
    it('retries an uncertain commit verbatim before saving new edits',()=>{
        const s=new EditorStore(document(),'r0');s.command('改',d=>d.title='新标题');const a=s.beginSave();
        s.saveFailed({status:0});s.command('再改',d=>d.title='更新标题');expect(s.beginSave()).toEqual(a);
        s.savedResponse({document:a.document,revision:'r1'});expect(s.model.title).toBe('更新标题');expect(s.beginSave().revision).toBe('r1');
    });
    it('conflicts pause saving until the inspected server revision is adopted',()=>{
        const s=new EditorStore(document(),'r0');s.command('改',d=>d.title='本地');s.beginSave();s.saveFailed({status:409});
        s.command('改',d=>d.title='本地继续');expect(s.beginSave()).toBeNull();
        s.adoptServer({document:document(),revision:'r2'},{keepLocal:true});expect(s.beginSave().revision).toBe('r2');expect(s.model.title).toBe('本地继续');
    });
    it('preserves the history restore endpoint for an uncertain retry',()=>{
        const s=new EditorStore(document(),'r0');s.command('恢复',d=>d.title='历史标题');
        const attempt=s.beginSave({restoreId:23});s.saveFailed({status:0});expect(s.beginSave()).toEqual(attempt);expect(attempt.restoreId).toBe(23);
    });
    it('keeps navigation out of history, coalesces typing and bounds history bytes',()=>{
        const s=new EditorStore(document(),'r0',{maxHistoryBytes:2000});s.select(['b1']);s.page(0);expect(s.undoStack).toHaveLength(0);
        s.command('写',d=>d.title='a',{coalesce:'title',now:10});s.command('写',d=>d.title='ab',{coalesce:'title',now:100});expect(s.undoStack).toHaveLength(1);
        for(let i=0;i<20;i++)s.command('改',d=>d.title=String(i));expect(s.undoStack.reduce((n,x)=>n+x.bytes,0)).toBeLessThanOrEqual(2000);
    });
});
describe('object traversal and geometry',()=>{
    it('copies internal actions and preserves diagram node IDs across all layout containers',()=>{
        const d={slides:[{id:'s',left:[{type:'diagram',id:'d',kind:'mindmap',children:[{id:'node',label:'节点'}]}],areas:[{blocks:[{type:'button',id:'btn',actions:[{do:'toggle',target:'d'},{do:'goto',slideId:'s'}]}]}]}]};
        const c=freshInstance(d);expect(c.slides[0].left[0].children[0].id).toBe('node');expect(locate(c,c.slides[0].left[0].id)).toBeDefined();
        expect(c.slides[0].areas[0].blocks[0].actions[0].target).toBe(c.slides[0].left[0].id);
        expect(c.slides[0].areas[0].blocks[0].actions[1].slideId).toBe(c.slides[0].id);
        expect(removeBlocks(c,[c.slides[0].left[0].id])).toBe(1);
    });
    it('uses inverse parent transforms for dragging rotated scaled groups',()=>{
        const group={type:'group',frame:{x:150,y:80,w:400,h:200,r:30},natural:{w:200,h:100}};
        const m=parentMatrix([group]),p={x:35,y:22};expect(transform(inverse(m),transform(m,p)).x).toBeCloseTo(p.x);
        const f={x:10,y:10,w:80,h:30,r:40};const moved=movedFrame(f,{x:30,y:15},[group],{snap:false});
        const a=corners(f,m)[0],b=corners(moved,m)[0];expect(b.x-a.x).toBeCloseTo(30);expect(b.y-a.y).toBeCloseTo(15);
    });
    it('keeps the opposite corner fixed when resizing a rotated object',()=>{
        const f={x:120,y:90,w:200,h:80,r:42};const original=corners(f);const target={x:original[2].x+40,y:original[2].y+50};
        const next=resizedFrame(f,target),actual=corners(next);expect(actual[0].x).toBeCloseTo(original[0].x);expect(actual[0].y).toBeCloseTo(original[0].y);expect(actual[2].x).toBeCloseTo(target.x);expect(actual[2].y).toBeCloseTo(target.y);
    });
});
