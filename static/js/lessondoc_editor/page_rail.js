import {clone,uid,freshInstance,walkBlocks} from './model.js';
import {el,button} from './ui.js';

const rails=new WeakMap();
const MAX_PAGES=40;

// All coordinates are relative to the scrolling track, including its two ends.
export function nearestPageGap(x,gaps,radius=56) {
    let nearest=null;
    gaps.forEach((position,index)=>{
        const distance=Math.abs(position-x);
        if(distance<radius&&(!nearest||distance<nearest.distance))nearest={index,position,distance,strength:Math.pow(1-distance/radius,1.35)};
    });
    return nearest;
}

export function insertPageAt(store,index) {
    if(store.ui.trial)throw new Error('请先返回编辑，再插入页面。');
    const slides=store.model.slides;
    if(!Array.isArray(slides)||!Number.isInteger(index)||index<0||index>slides.length)throw new Error('插入位置已变化，请重新选择页面间隙。');
    if(slides.length>=MAX_PAGES)throw new Error('每课最多 40 页，请将后续内容拆分到其他课次。');
    const id=uid('s');
    store.command('插入页面',model=>model.slides.splice(index,0,{id,layout:'content',title:'新页面',empty:true,blocks:[]}));
    store.page(index);
    return id;
}

class PageRail {
    constructor(root,store,onError) {
        this.root=root;this.store=store;this.onError=onError;this.cards=new Map();this.gaps=[];
        this.pointer=null;this.keyboardGap=null;this.selectedId=null;this.frame=0;
        this.touch=matchMedia('(hover: none)').matches;
        this.abort=new AbortController();const options={signal:this.abort.signal};
        this.track=el('div','lde-page-track');
        this.marker=button('',()=>this.insert(Number(this.marker.dataset.index)),'lde-page-insert');
        const plus=el('span','lde-page-insert-plus','+'),line=el('span','lde-page-insert-line');
        plus.setAttribute('aria-hidden','true');line.setAttribute('aria-hidden','true');
        this.marker.append(plus,line);this.track.append(this.marker);root.replaceChildren(this.track);
        root.addEventListener('pointermove',event=>{
            if(event.pointerType==='touch')return;
            this.touch=false;this.keyboardGap=null;this.pointer={x:event.clientX,y:event.clientY};this.schedule();
        },options);
        root.addEventListener('pointerleave',()=>{this.pointer=null;this.schedule();},options);
        root.addEventListener('pointerdown',event=>{
            if(event.pointerType==='touch'){this.touch=true;this.pointer=null;this.keyboardGap=null;this.schedule();}
        },options);
        root.addEventListener('scroll',()=>this.schedule(),{...options,passive:true});
        root.addEventListener('focusin',event=>{
            if(event.target===this.marker&&this.marker.matches(':focus-visible')){
                this.pointer=null;this.keyboardGap??=Math.min(store.ui.slide+1,store.model.slides.length);this.schedule();
            }
        },options);
        root.addEventListener('focusout',event=>{if(event.target===this.marker){this.keyboardGap=null;this.schedule();}},options);
        root.addEventListener('keydown',event=>this.keydown(event),options);
        this.observer=new ResizeObserver(()=>this.schedule(true));this.observer.observe(root);this.observer.observe(this.track);
        window.addEventListener('pagehide',event=>{if(!event.persisted)this.destroy();},options);
    }
    update() {
        const {slides}=this.store.model,selected=slides[this.store.ui.slide]?.id,ordered=[];
        slides.forEach((slide,index)=>{
            let card=this.cards.get(slide.id);
            if(!card){
                card=button('',()=>this.store.page(Number(card.dataset.index)),'lde-page-thumb');
                card.append(el('small'),el('strong'),el('span'));this.cards.set(slide.id,card);
            }
            card.dataset.index=String(index);
            card.setAttribute('aria-label','第 '+(index+1)+' 页 '+(slide.title||''));
            card.setAttribute('aria-current',slide.id===selected?'page':'false');
            card.classList.toggle('is-current',slide.id===selected);card.tabIndex=slide.id===selected?0:-1;
            let excerpt='';walkBlocks(slide,block=>{if(!excerpt)excerpt=block.md||block.label||block.q||block.code||'';});
            [String(index+1),slide.title||'未命名页面',excerpt.slice(0,55)].forEach((text,i)=>{if(card.children[i].textContent!==text)card.children[i].textContent=text;});
            ordered.push(card);
        });
        const ids=new Set(slides.map(slide=>slide.id));
        for(const[id,card]of this.cards)if(!ids.has(id)){card.remove();this.cards.delete(id);}
        // Preserve nodes, keyboard focus and scroll position during autosave.
        ordered.forEach((card,index)=>{if(this.track.children[index]!==card)this.track.insertBefore(card,this.track.children[index]||this.marker);});
        this.marker.disabled=slides.length>=MAX_PAGES;
        this.root.title=this.marker.disabled?'已达到每课 40 页上限，请拆分到其他课次。':'靠近页面间隙插入新页；也可聚焦导航栏后按 Insert。';
        if(selected!==this.selectedId){this.revealSelected=true;this.selectedId=selected;}
        this.schedule(true);
    }
    measure() {
        const cards=[...this.track.querySelectorAll('.lde-page-thumb')];
        if(!cards.length){this.gaps=[];return;}
        const gap=parseFloat(getComputedStyle(this.track).columnGap)||18;
        this.gaps=[cards[0].offsetLeft-gap/2,...cards.map((card,index)=>index===cards.length-1?card.offsetLeft+card.offsetWidth+gap/2:(card.offsetLeft+card.offsetWidth+cards[index+1].offsetLeft)/2)];
    }
    schedule(measure=false) {
        this.needsMeasure||=measure;if(this.frame)return;
        this.frame=requestAnimationFrame(()=>{
            this.frame=0;if(!this.root.isConnected)return;
            if(this.needsMeasure){this.measure();this.needsMeasure=false;}
            if(this.revealSelected){this.revealSelected=false;const card=this.cards.get(this.selectedId);if(card)this.reveal(card.offsetLeft-26,card.offsetLeft+card.offsetWidth+26);}
            this.paint();
        });
    }
    reveal(left,right) {
        const root=this.root;
        if(left<root.scrollLeft)root.scrollLeft=Math.max(0,left);
        else if(right>root.scrollLeft+root.clientWidth)root.scrollLeft=right-root.clientWidth;
    }
    paint() {
        let target=null;
        if(!this.store.ui.trial){
            if(this.pointer){
                const bounds=this.root.getBoundingClientRect();
                if(this.pointer.x>=bounds.left&&this.pointer.x<=bounds.right&&this.pointer.y>=bounds.top&&this.pointer.y<bounds.top+this.root.clientHeight){
                    target=nearestPageGap(this.pointer.x-this.track.getBoundingClientRect().left,this.gaps);
                }
            }else if(this.keyboardGap!==null||this.touch){
                const index=Math.min(this.keyboardGap??this.store.ui.slide+1,this.gaps.length-1);
                if(index>=0)target={index,position:this.gaps[index],strength:1};
            }
        }
        const index=target?.index??Math.min(this.store.ui.slide+1,this.gaps.length-1);
        const label=index===0?'在第一页前插入新页面':index===this.store.model.slides.length?'在最后一页后插入新页面':'在第 '+index+' 页与第 '+(index+1)+' 页之间插入新页面';
        this.marker.dataset.index=String(index);
        this.marker.setAttribute('aria-label',this.marker.disabled?'已达到每课 40 页上限':label);
        this.marker.title=this.marker.disabled?this.root.title:label+'（方向键选择位置，Enter 插入）';
        if(this.gaps[index]!==undefined)this.marker.style.left=this.gaps[index]+'px';
        this.marker.style.setProperty('--lde-insert-strength',String(target?.strength||0));
        this.marker.classList.toggle('is-visible',!!target);
    }
    insert(index) {
        try{
            this.pointer=null;this.keyboardGap=null;
            const id=insertPageAt(this.store,index);
            this.cards.get(id)?.focus({preventScroll:true});
        }catch(error){this.onError(error);}
        this.schedule(true);
    }
    keydown(event) {
        if(event.ctrlKey||event.metaKey||event.altKey)return;
        if(event.key==='Insert'||event.key==='+'){
            event.preventDefault();event.stopPropagation();this.insert(this.store.ui.slide+1);return;
        }
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();event.stopPropagation();this.pointer=null;
        const atMarker=event.target===this.marker,max=this.store.model.slides.length-(atMarker?0:1);
        const current=atMarker?(this.keyboardGap??Number(this.marker.dataset.index)):this.store.ui.slide;
        const next=event.key==='Home'?0:event.key==='End'?max:Math.max(0,Math.min(max,current+(event.key==='ArrowLeft'?-1:1)));
        if(atMarker){this.keyboardGap=next;this.reveal(this.gaps[next]-20,this.gaps[next]+20);this.schedule();}
        else{this.store.page(next);this.cards.get(this.store.model.slides[next].id)?.focus({preventScroll:true});}
    }
    destroy() {this.abort.abort();this.observer.disconnect();cancelAnimationFrame(this.frame);}
}

export function renderPageRail(root,store,onError) {
    if(store.model.kind==='home'){
        rails.get(root)?.destroy();rails.delete(root);root.replaceChildren(el('span','lde-muted','课程首页 · 长页面'));return;
    }
    let rail=rails.get(root);if(!rail){rail=new PageRail(root,store,onError);rails.set(root,rail);}rail.update();
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
