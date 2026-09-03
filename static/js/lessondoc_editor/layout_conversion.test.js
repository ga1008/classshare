import {it,expect} from 'vitest';
import {convertLayout} from './layout_conversion.js';
import {walkBlocks} from './model.js';

it('preserves all text and IDs through two-column, grid and canvas conversion',()=>{
    const initial={id:'s',layout:'two-col',left:[{id:'a',type:'text',md:'左'}],right:[{id:'b',type:'text',md:'右'}],overlays:[{id:'c',type:'callout',md:'叠加',frame:{x:20,y:30,w:200,h:100}}]};
    let slide=initial;for(const layout of ['grid','canvas','content','two-col']){
        slide=convertLayout(slide,layout,{a:{x:60,y:100,w:500,h:100},b:{x:680,y:100,w:500,h:100}});
        const ids=[];walkBlocks(slide,b=>ids.push(b.id));expect(ids.sort()).toEqual(['a','b','c']);
    }
});
it('retains group frames and child scale when converting a canvas to flow',()=>{
    const group={id:'g',type:'group',frame:{x:100,y:100,w:400,h:200,r:20},natural:{w:200,h:100},children:[{id:'c',type:'text',md:'组合正文',frame:{x:10,y:10,w:180,h:80}}]};
    const next=convertLayout({id:'s',layout:'canvas',objects:[group]},'content');expect(next.overlays[0]).toEqual(group);
});
it('converts section explanations into visible blocks, not stranded metadata',()=>{
    const next=convertLayout({id:'s',layout:'section',title:'章节',hint:'关键解释'},'content');expect(next.blocks.some(b=>b.md==='关键解释')).toBe(true);
});
