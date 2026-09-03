import {describe,it,expect} from 'vitest';
import {nearestPageGap,insertPageAt} from './page_rail.js';
import {EditorStore} from './state.js';

const document=()=>({kind:'lesson',slides:[
    {id:'a',layout:'content',blocks:[{id:'jump',type:'button',actions:[{do:'goto',slideId:'b',slide:2}]}]},
    {id:'b',layout:'content',blocks:[]},
]});

describe('page insertion targets',()=>{
    it('chooses just the nearest gap, fades with distance and disappears over card centers',()=>{
        const gaps=[17,168,319];
        expect(nearestPageGap(168,gaps)).toMatchObject({index:1,strength:1});
        expect(nearestPageGap(150,gaps).strength).toBeGreaterThan(nearestPageGap(120,gaps).strength);
        expect(nearestPageGap(92,gaps)).toBeNull();
        expect(nearestPageGap(17,gaps)).toMatchObject({index:0});
        expect(nearestPageGap(319,gaps)).toMatchObject({index:2});
    });
    it.each([0,1,2])('inserts at boundary %i independently of the current page and supports undo/redo',index=>{
        const store=new EditorStore(document(),'r1');store.page(1);
        const id=insertPageAt(store,index);
        expect(store.model.slides.map(s=>s.id)).toEqual([...['a','b'].slice(0,index),id,...['a','b'].slice(index)]);
        expect(store.ui.slide).toBe(index);
        expect(store.model.slides.find(s=>s.id==='a').blocks[0].actions[0].slideId).toBe('b');
        expect(store.undoStack).toHaveLength(1);
        store.undo();expect(store.model).toEqual(document());
        store.redo();expect(store.model.slides[index].id).toBe(id);
    });
    it('rejects stale positions, preview changes and the page limit without modifying the document',()=>{
        const store=new EditorStore(document(),'r1');
        for(const index of [-1,3,NaN,0.5])expect(()=>insertPageAt(store,index)).toThrow('插入位置');
        store.ui.trial=true;expect(()=>insertPageAt(store,1)).toThrow('返回编辑');
        store.ui.trial=false;store.model.slides=Array.from({length:40},(_,i)=>({id:'s'+i,layout:'content',blocks:[]}));
        expect(()=>insertPageAt(store,40)).toThrow('最多 40 页');
        expect(store.model.slides).toHaveLength(40);expect(store.undoStack).toHaveLength(0);
    });
});
