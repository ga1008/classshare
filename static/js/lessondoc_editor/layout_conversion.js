import {clone,uid} from './model.js';
import {adaptSizing} from './resize_commands.js';

export function flowBlocks(slide) {
    if(slide.layout==='two-col')return [...(slide.left||[]),...(slide.right||[])];
    if(slide.layout==='grid')return(slide.areas||[]).flatMap(a=>a.blocks||[]);
    return [...(slide.blocks||[])];
}
export function convertLayout(source,layout,measurements={}) {
    const slide=clone(source);if(slide.layout===layout)return slide;
    const previous=slide.layout;let flow=flowBlocks(slide),positioned=[...(slide.objects||[]),...(slide.overlays||[])];
    if(previous==='section'&&source.hint)flow.unshift({id:uid(),type:'text',md:source.hint});
    if(previous==='end')for(const key of ['summary','nextUp'])if(source[key])flow.push({id:uid(),type:'text',md:source[key]});
    // These fields have become ordinary content. Keeping both representations
    // would duplicate text after converting back and forth.
    if(previous==='section')delete slide.hint;
    if(previous==='end'){delete slide.summary;delete slide.nextUp;}
    for(const key of ['blocks','left','right','areas','objects','overlays'])delete slide[key];
    slide.layout=layout;
    if(layout==='canvas') {
        slide.objects=[...flow.map((b,i)=>{const frame=b.frame||measurements[b.id]||{x:80+(i%2)*570,y:120+Math.floor(i/2)*150,w:b.flowFrame?.w||540,h:b.flowFrame?.h||130};delete b.flowFrame;return {...b,frame};}),...positioned];
    }else if(['title','section'].includes(layout)) {
        // The title renderer does not render flow blocks. Keep them as overlays.
        slide.overlays=[...flow.map((b,i)=>{const frame=b.frame||measurements[b.id]||{x:80,y:360+i*110,w:b.flowFrame?.w||1120,h:b.flowFrame?.h||100};delete b.flowFrame;return {...b,frame};}),...positioned];
    }else {
        if(previous==='canvas') { flow.push(...positioned.filter(b=>b.type!=='group').sort((a,b)=>(a.frame?.y||0)-(b.frame?.y||0)||(a.frame?.x||0)-(b.frame?.x||0)).map(b=>adaptSizing(clone(b),false)));positioned=positioned.filter(b=>b.type==='group'); }
        if(layout==='two-col'){const half=Math.ceil(flow.length/2);slide.left=flow.slice(0,half);slide.right=flow.slice(half);}
        else if(layout==='grid')slide.areas=flow.map(b=>({blocks:[b]}));
        else slide.blocks=flow;
        if(positioned.length)slide.overlays=positioned;
    }
    slide.empty=!flow.length&&!positioned.length&&!(slide.objects||[]).length;
    return slide;
}
