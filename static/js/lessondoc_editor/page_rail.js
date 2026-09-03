import {clone,uid,freshInstance,walkBlocks} from './model.js';
import {el,button} from './ui.js';
export function renderPageRail(root,store,onError) {
    root.replaceChildren();if(store.model.kind==='home'){root.append(el('span','lde-muted','课程首页 · 长页面'));return;}
    for(const[ index,slide]of store.model.slides.entries()) {
        const b=button('',()=>store.page(index),'lde-page-thumb'+(index===store.ui.slide?' is-current':''));b.setAttribute('aria-label','第 '+(index+1)+' 页 '+(slide.title||''));b.setAttribute('aria-current',index===store.ui.slide?'page':'false');
        b.append(el('small','',String(index+1)),el('strong','',slide.title||'未命名页面'));
        let excerpt='';walkBlocks(slide,block=>{if(!excerpt)excerpt=block.md||block.label||block.q||block.code||'';});b.append(el('span','',excerpt.slice(0,55)));root.append(b);
    }
    root.append(button('+ 页面',()=>{
        if(store.model.slides.length>=40){onError(new Error('每课最多 40 页，请将后续内容拆分到其他课次。'));return;}
        const index=store.ui.slide+1;store.command('添加页面',d=>d.slides.splice(index,0,{id:uid('s'),layout:'content',title:'新页面',empty:true,blocks:[]}));store.page(index);
    },'lde-page-add'));
}
export function pageCommand(store,operation) {
    const index=store.ui.slide,slide=store.model.slides[index];if(!slide)return;
    if(operation==='duplicate'){
        if(store.model.slides.length>=40)throw new Error('每课最多 40 页');
        store.command('复制页面',model=>{const copy=freshInstance({slides:[clone(slide)]}).slides[0];model.slides.splice(index+1,0,copy);});store.page(index+1);
    }else if(operation==='delete'){
        if(store.model.slides.length<=1)throw new Error('至少保留一页，可以将本页内容清空。');
        store.command('删除页面',model=>{const removed=model.slides.splice(index,1)[0],ids=new Set();walkBlocks(removed,b=>ids.add(b.id));walkBlocks(model,b=>{if(b.actions)b.actions=b.actions.filter(a=>a.slideId!==removed.id&&!ids.has(a.target));});});store.page(Math.min(index,store.model.slides.length-1));
    }else{
        const to=index+(operation==='previous'?-1:1);if(to<0||to>=store.model.slides.length)return;
        store.command('移动页面',model=>{[model.slides[index],model.slides[to]]=[model.slides[to],model.slides[index]];});store.page(to);
    }
}
