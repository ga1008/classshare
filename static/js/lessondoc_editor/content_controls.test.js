import {it,expect} from 'vitest';
import {moveCodewalkLine} from './content_controls.js';
import {parseStagesText,stagesToText} from '../lessondoc_stages.js';
import {convertLayout} from './layout_conversion.js';

it('remaps codewalk references and rejects a reference before its source without mutation',()=>{
    const block={lines:[{code:'A'},{code:'B'},{ref:0},{ref:1}]};
    moveCodewalkLine(block,1,0);expect(block.lines).toEqual([{code:'B'},{code:'A'},{ref:1},{ref:0}]);
    const before=structuredClone(block);expect(()=>moveCodewalkLine(block,2,0)).toThrow();expect(block).toEqual(before);
});
it('bounds stage ranges and roundtrips Chinese punctuation',()=>{
    const stages=parseStagesText('基础：1 - 3，5\n进阶: 7~6');
    expect(stages[0].lessons).toEqual([1,2,3,5]);expect(parseStagesText(stagesToText(stages))).toEqual(stages);
    expect(()=>parseStagesText('过大: 1-999999999')).toThrow();expect(()=>parseStagesText('有误')).toThrow();
});
it('does not duplicate generated section or closing content after roundtrips',()=>{
    let slide={id:'s',layout:'end',summary:'小结',nextUp:'预告'};
    for(const type of ['content','end','content'])slide=convertLayout(slide,type);
    expect(slide.blocks.map(b=>b.md)).toEqual(['小结','预告']);
});
