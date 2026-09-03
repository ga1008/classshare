import {describe,it,expect} from 'vitest';
import {nearestPageGap,insertPageAt,pageCommand} from './page_rail.js';
import {pageRailTarget,PageRailPress} from './page_rail_pointer.js';
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

describe('card dragging and gap clicks',()=>{
    const cards=[{id:'a',index:0,left:26,right:159,top:26,bottom:108},{id:'b',index:1,left:177,right:310,top:26,bottom:108}];
    it('gives card faces priority, accepts whole gaps and rejects blank space',()=>{
        expect(pageRailTarget(157,60,cards)).toMatchObject({kind:'card',id:'a'});
        expect(pageRailTarget(179,60,cards)).toMatchObject({kind:'card',id:'b'});
        expect(pageRailTarget(160,60,cards)).toEqual({kind:'gap',index:1});
        expect(pageRailTarget(176,60,cards)).toEqual({kind:'gap',index:1});
        expect(pageRailTarget(10,60,cards)).toEqual({kind:'gap',index:0});
        expect(pageRailTarget(325,60,cards)).toEqual({kind:'gap',index:2});
        for(const[x,y]of[[50,10],[500,60],[168,125],[-1,60]])expect(pageRailTarget(x,y,cards)).toBeNull();
    });
    it('tolerates click jitter, then follows horizontal movement one to one',()=>{
        const press=new PageRailPress({kind:'card'},100,60,200,1000);
        press.move(103,62);expect(press.moved).toBe(false);expect(press.scrollLeft).toBe(200);
        press.move(70,60);expect(press.moved).toBe(true);expect(press.scrollLeft).toBe(230);
        press.move(90,60);expect(press.scrollLeft).toBe(210);
        press.move(100,60);expect(press.moved).toBe(true); // returning to the origin is still a drag
    });
    it('clamps both edges and immediately responds when reversing direction',()=>{
        const press=new PageRailPress({kind:'card'},100,60,20,200);
        press.move(180,60);expect(press.scrollLeft).toBe(0);
        press.move(175,60);expect(press.scrollLeft).toBe(5);
        press.move(-100,60);expect(press.scrollLeft).toBe(200);
        press.move(-94,60);expect(press.scrollLeft).toBe(194);
    });
    it('a gap drag never scrolls, even after crossing onto a card',()=>{
        const press=new PageRailPress({kind:'gap',index:1},168,60,200,1000);
        press.move(300,60);expect(press.moved).toBe(true);expect(press.scrollLeft).toBe(200);
        press.move(168,60);expect(press.moved).toBe(true);expect(press.scrollLeft).toBe(200);
    });
});

describe('page removal and navigation references',()=>{
    const deck=()=>({kind:'lesson',globals:[{id:'global',type:'text',excludeSlides:['b','c'],actions:[{do:'goto',slide:3}]}],slides:[
        {id:'a',layout:'content',blocks:[{id:'jump',type:'button',actions:[{do:'goto',slide:2},{do:'goto',slideId:'c',slide:3},{do:'show',target:'inside'}]}]},
        {id:'b',layout:'content',blocks:[{id:'group',type:'group',children:[{id:'inside',type:'text'}]}]},
        {id:'c',layout:'content',blocks:[]},
    ]});
    it('deletes the selected page, compacts numbering and removes only broken references in one undo step',()=>{
        const store=new EditorStore(deck(),'r1');store.page(1);pageCommand(store,'delete');
        expect(store.model.slides.map(s=>s.id)).toEqual(['a','c']);expect(store.ui.slide).toBe(1);
        expect(store.model.slides[0].blocks[0].actions).toEqual([{do:'goto',slideId:'c',slide:2}]);
        expect(store.model.globals[0]).toMatchObject({excludeSlides:['c'],actions:[{do:'goto',slideId:'c',slide:2}]});
        expect(store.undoStack).toHaveLength(1);store.undo();expect(store.model).toEqual(deck());
        store.redo();expect(store.model.slides.map(s=>s.id)).toEqual(['a','c']);
    });
    it('keeps numbered jumps attached to their page during insertion, reordering and duplication',()=>{
        const store=new EditorStore(deck(),'r1');insertPageAt(store,0);
        expect(store.model.globals[0].actions[0]).toMatchObject({slideId:'c',slide:4});
        store.page(3);pageCommand(store,'previous');
        expect(store.model.globals[0].actions[0]).toMatchObject({slideId:'c',slide:3});
        pageCommand(store,'duplicate');expect(store.model.globals[0].actions[0]).toMatchObject({slideId:'c',slide:3});
    });
    it('selects the remaining last page and protects the final page and preview mode',()=>{
        const store=new EditorStore(document(),'r1');store.page(1);pageCommand(store,'delete');
        expect(store.ui.slide).toBe(0);expect(store.model.slides).toHaveLength(1);
        expect(()=>pageCommand(store,'delete')).toThrow('至少保留一页');
        store.ui.trial=true;
        for(const operation of ['delete','duplicate','next'])expect(()=>pageCommand(store,operation)).toThrow('返回编辑');
        expect(store.undoStack).toHaveLength(1);
    });
});
