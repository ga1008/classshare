import {clone,uid,freshInstance,walkBlocks} from './model.js';
import {el,button} from './ui.js';
import {pageRailTarget,PageRailPress} from './page_rail_pointer.js';

const rails=new WeakMap();
const MAX_PAGES=40;

function changePages(store,label,mutate) {
    store.command(label,model=>{
        // Legacy numbered jumps must keep pointing to the same page when rows shift.
        walkBlocks(model,block=>{for(const action of block.actions||[])if(action.do==='goto'&&!action.slideId){const target=model.slides[action.slide-1];if(target)action.slideId=target.id;}});
        mutate(model);
        const positions=new Map(model.slides.map((slide,index)=>[slide.id,index+1]));
        walkBlocks(model,block=>{
            if(block.actions)block.actions=block.actions.filter(action=>{
                if(action.do!=='goto')return true;
                if(!positions.has(action.slideId))return false;
                action.slide=positions.get(action.slideId);return true;
            });
            if(block.excludeSlides)block.excludeSlides=block.excludeSlides.filter(id=>positions.has(id));
        });
    });
}

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
    changePages(store,'插入页面',model=>model.slides.splice(index,0,{id,layout:'content',title:'新页面',empty:true,blocks:[]}));
    store.page(index);
    return id;
}

class PageRail {
    constructor(root,store,onError) {
        this.root=root;this.store=store;this.onError=onError;this.cards=new Map();this.gaps=[];
        this.pointer=null;this.keyboardGap=null;this.selectedId=null;this.frame=0;this.cardBounds=[];
        this.press=null;this.suppressClick=false;this.pendingScroll=null;this.animations=[];
        this.touch=matchMedia('(hover: none)').matches;
        this.abort=new AbortController();const options={signal:this.abort.signal};
        this.track=el('div','lde-page-track');
        this.marker=button('',()=>{},'lde-page-insert');
        const plus=el('span','lde-page-insert-plus','+'),line=el('span','lde-page-insert-line');
        plus.setAttribute('aria-hidden','true');line.setAttribute('aria-hidden','true');
        this.marker.append(plus,line);this.track.append(this.marker);root.replaceChildren(this.track);
        root.addEventListener('pointermove',event=>this.pointermove(event),options);
        root.addEventListener('pointerleave',()=>{this.pointer=null;this.schedule();},options);
        root.addEventListener('pointerdown',event=>this.pointerdown(event),options);
        window.addEventListener('pointerup',event=>this.endPress(event),options);
        window.addEventListener('pointercancel',event=>this.endPress(event,true),options);
        root.addEventListener('lostpointercapture',event=>this.endPress(event,true),options);
        window.addEventListener('blur',()=>this.endPress(null,true),options);
        root.addEventListener('click',event=>this.click(event),{...options,capture:true});
        root.addEventListener('dragstart',event=>event.preventDefault(),options);
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
        if(this.press&&(this.press.serial!==this.store.serial||selected!==this.selectedId))this.endPress(null,true);
        const changed=slides.length!==this.cards.size||slides.some((s,i)=>this.track.children[i]!==this.cards.get(s.id));
        if(changed&&this.cardBounds.length)this.reflow=new Map(this.cardBounds.map(c=>[c.id,c.left]));
        slides.forEach((slide,index)=>{
            let card=this.cards.get(slide.id);
            if(!card){
                card=button('',()=>{},'lde-page-thumb');
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
        this.root.title=this.marker.disabled?'已达到每课 40 页上限，请拆分到其他课次。':'单击卡片切页，按住卡片左右拖动；单击间隙插入新页。';
        if(selected!==this.selectedId){this.revealSelected=true;this.selectedId=selected;}
        this.schedule(true);
    }
    measure() {
        const cards=[...this.track.querySelectorAll('.lde-page-thumb')];
        this.cardBounds=cards.map((card,index)=>({id:this.store.model.slides[index].id,index,left:card.offsetLeft,right:card.offsetLeft+card.offsetWidth,top:card.offsetTop,bottom:card.offsetTop+card.offsetHeight}));
        if(!cards.length){this.gaps=[];return;}
        const gap=parseFloat(getComputedStyle(this.track).columnGap)||18;
        this.gaps=[cards[0].offsetLeft-gap/2,...cards.map((card,index)=>index===cards.length-1?card.offsetLeft+card.offsetWidth+gap/2:(card.offsetLeft+card.offsetWidth+cards[index+1].offsetLeft)/2)];
    }
    schedule(measure=false) {
        this.needsMeasure||=measure;if(this.frame)return;
        this.frame=requestAnimationFrame(()=>{
            this.frame=0;if(!this.root.isConnected)return;
            if(this.needsMeasure){this.measure();this.needsMeasure=false;}
            this.flushScroll();
            if(this.revealSelected){this.revealSelected=false;const card=this.cards.get(this.selectedId);if(card)this.reveal(card.offsetLeft-26,card.offsetLeft+card.offsetWidth+26);}
            this.animateReflow();
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
        if(!this.store.ui.trial&&!this.press?.moved){
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
    hit(event) {
        const root=this.root.getBoundingClientRect(),track=this.track.getBoundingClientRect();
        if(event.clientX<root.left||event.clientX>root.right||event.clientY<root.top||event.clientY>=root.top+this.root.clientHeight)return null;
        if(this.needsMeasure){this.measure();this.needsMeasure=false;}
        const target=pageRailTarget(event.clientX-track.left,event.clientY-track.top,this.cardBounds);
        if(target)return target;
        if(this.marker.contains(event.target)&&this.marker.classList.contains('is-visible'))return {kind:'gap',index:Number(this.marker.dataset.index)};
        return null;
    }
    pointerdown(event) {
        this.suppressClick=false;this.clickTarget=null;
        if(event.pointerType==='touch'){this.touch=true;this.pointer=null;this.keyboardGap=null;this.schedule();return;}
        if(event.button!==0||!event.isPrimary||this.press)return;
        this.cancelAnimations();
        const target=this.hit(event);if(!target)return;
        event.preventDefault();
        this.press=new PageRailPress(target,event.clientX,event.clientY,this.root.scrollLeft,this.root.scrollWidth-this.root.clientWidth);
        this.press.pointerId=event.pointerId;this.press.serial=this.store.serial;
        this.root.setPointerCapture(event.pointerId);
    }
    pointermove(event) {
        if(event.pointerType==='touch')return;
        this.touch=false;this.keyboardGap=null;this.pointer={x:event.clientX,y:event.clientY};
        const press=this.press;
        if(press&&event.pointerId===press.pointerId){
            if(!(event.buttons&1)){this.endPress(event,true);return;}
            press.move(event.clientX,event.clientY);
            if(press.moved){
                event.preventDefault();
                if(!this.root.hasPointerCapture(event.pointerId))this.root.setPointerCapture(event.pointerId);
                if(press.target.kind==='card'){this.pendingScroll=press.scrollLeft;this.root.classList.add('is-dragging');}
            }
        }
        this.schedule();
    }
    flushScroll() {if(this.pendingScroll!==null){this.root.scrollLeft=this.pendingScroll;this.pendingScroll=null;}}
    endPress(event,cancelled=false) {
        const press=this.press;if(!press||(event&&event.pointerId!==press.pointerId))return;
        if(event&&!cancelled)press.move(event.clientX,event.clientY);
        if(press.moved&&press.target.kind==='card')this.pendingScroll=press.scrollLeft;
        this.flushScroll();this.suppressClick=cancelled||press.moved;this.clickTarget=this.suppressClick?null:press.target;this.press=null;
        if(this.root.hasPointerCapture(press.pointerId))this.root.releasePointerCapture(press.pointerId);
        this.root.classList.remove('is-dragging');this.pointer=null;this.schedule();
    }
    click(event) {
        if(event.button!==0)return;
        event.preventDefault();event.stopImmediatePropagation();
        if(event.detail&&this.suppressClick)return;
        let target;
        if(event.detail){target=this.clickTarget??this.hit(event);this.clickTarget=null;}
        else{
            const card=event.target.closest('.lde-page-thumb');
            target=card?{kind:'card',index:Number(card.dataset.index)}:this.marker.contains(event.target)?{kind:'gap',index:Number(this.marker.dataset.index)}:null;
        }
        if(target?.kind==='card'){
            this.store.page(target.index);this.cards.get(this.store.model.slides[target.index].id)?.focus({preventScroll:true});
        }else if(target?.kind==='gap'&&!this.store.ui.trial&&!this.marker.disabled)this.insert(target.index);
    }
    cancelAnimations() {for(const animation of this.animations)animation.cancel();this.animations=[];}
    animateReflow() {
        const prior=this.reflow;this.reflow=null;if(!prior)return;
        this.cancelAnimations();if(this.press||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
        for(const bounds of this.cardBounds){
            if(bounds.right<this.root.scrollLeft||bounds.left>this.root.scrollLeft+this.root.clientWidth)continue;
            const before=prior.get(bounds.id),card=this.cards.get(bounds.id),delta=(before??bounds.left)-bounds.left;
            if(delta||before===undefined)this.animations.push(card.animate(before===undefined?[{opacity:0,transform:'scale(.96)'},{opacity:1,transform:'scale(1)'}]:[{transform:'translateX('+delta+'px)'},{transform:'translateX(0)'}],{duration:160,easing:'cubic-bezier(.2,.8,.2,1)'}));
        }
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
        if(event.key==='Escape'&&this.press){event.preventDefault();event.stopPropagation();this.endPress(null,true);return;}
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
    destroy() {this.endPress(null,true);this.abort.abort();this.observer.disconnect();cancelAnimationFrame(this.frame);this.cancelAnimations();}
}

export function renderPageRail(root,store,onError) {
    if(store.model.kind==='home'){
        rails.get(root)?.destroy();rails.delete(root);root.replaceChildren(el('span','lde-muted','课程首页 · 长页面'));return;
    }
    let rail=rails.get(root);if(!rail){rail=new PageRail(root,store,onError);rails.set(root,rail);}rail.update();
}
export function pageCommand(store,operation) {
    if(store.ui.trial)throw new Error('请先返回编辑，再修改页面。');
    const index=store.ui.slide,slide=store.model.slides[index];if(!slide)return;
    if(operation==='duplicate'){
        if(store.model.slides.length>=40)throw new Error('每课最多 40 页');
        changePages(store,'复制页面',model=>{const copy=freshInstance({slides:[clone(model.slides[index])]}).slides[0];model.slides.splice(index+1,0,copy);});store.page(index+1);
    }else if(operation==='delete'){
        if(store.model.slides.length<=1)throw new Error('至少保留一页，可以将本页内容清空。');
        changePages(store,'删除页面',model=>{const removed=model.slides.splice(index,1)[0],ids=new Set();walkBlocks(removed,b=>ids.add(b.id));walkBlocks(model,b=>{if(b.actions)b.actions=b.actions.filter(a=>a.slideId!==removed.id&&!ids.has(a.target));});});store.page(Math.min(index,store.model.slides.length-1));
    }else{
        const to=index+(operation==='previous'?-1:1);if(to<0||to>=store.model.slides.length)return;
        changePages(store,'移动页面',model=>{[model.slides[index],model.slides[to]]=[model.slides[to],model.slides[index]];});store.page(to);
    }
}
