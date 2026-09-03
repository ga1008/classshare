import {clone,locate,rootSelection,at} from './model.js';
import {bounds,corners,parentMatrix,inverse,transform,movedSelection,resizedFrame,overlaps} from './geometry.js';
import {flowDestinations,moveFlow} from './object_commands.js';

const svgNS='http://www.w3.org/2000/svg';
export class CanvasController {
    constructor(bridge,store,onError,onDrop) {
        this.bridge=bridge;this.store=store;this.onError=onError;this.gesture=null;bridge.controller=this;
        this.doc=bridge.document;this.abort=new AbortController();const options={capture:true,signal:this.abort.signal};
        this.doc.addEventListener('pointerdown',e=>this.down(e),options);
        this.doc.addEventListener('pointermove',e=>this.move(e),options);
        this.doc.addEventListener('pointerup',e=>this.up(e),options);
        this.doc.addEventListener('pointercancel',()=>this.cancel(),options);
        this.doc.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.gesture){e.preventDefault();this.cancel();}},options);
        this.doc.addEventListener('dblclick',e=>this.enter(e),options);
        this.doc.addEventListener('dragover',e=>{if(!store.ui.trial&&e.dataTransfer?.types.includes('application/x-lessondoc-block')){e.preventDefault();e.dataTransfer.dropEffect='copy';}},options);
        this.doc.addEventListener('drop',e=>{const type=e.dataTransfer?.getData('application/x-lessondoc-block');if(!type||store.ui.trial)return;e.preventDefault();const point=bridge.point(e);onDrop?.(type,{point,targetId:this.pick(point)});},options);
        bridge.window.addEventListener('blur',()=>this.cancel(),{signal:this.abort.signal});
        this.draw();
    }
    eligible(id) {
        const item=locate(this.store.model,id);if(!item)return false;
        const parent=this.store.ui.groupPath.at(-1);
        const groups=item.ancestors.filter(b=>b.type==='group');
        return parent?groups.at(-1)?.id===parent:groups.length===0;
    }
    pick(point) {
        const hit=this.bridge.api.hitTest(point.x,point.y);if(!hit)return null;
        return hit.chain.find(id=>this.eligible(id))||null;
    }
    down(e) {
        if(e.button!==0||this.store.ui.trial||this.gesture)return;
        const p=this.bridge.point(e),handle=e.target.closest?.('[data-lde-handle]');
        let id=handle?.dataset.id||this.pick(p),mode=handle?.dataset.ldeHandle;
        if(id&&!mode) {
            const selected=this.store.ui.selection;
            if(e.shiftKey){this.store.select(selected.includes(id)?selected.filter(x=>x!==id):[...selected,id]);return;}
            if(!selected.includes(id))this.store.select([id]);
        } else if(!id&&!e.shiftKey)this.store.select([]);
        const selected=rootSelection(this.store.model,this.store.ui.selection);
        mode??=id?(selected.every(x=>x.block.frame)?'move':'flow'):'marquee';
        if(mode==='flow'&&selected.length!==1)return;
        this.gesture={start:p,point:p,mode,selected:selected.map(x=>({...x,block:clone(x.block),ancestors:clone(x.ancestors)})),frames:new Map(),active:false,shift:e.shiftKey};
        this.doc.documentElement.setPointerCapture?.(e.pointerId);e.preventDefault();
    }
    move(e) {
        const g=this.gesture;if(!g)return;
        const point=this.bridge.point(e);g.point=point;
        if(!g.active&&Math.hypot(point.x-g.start.x,point.y-g.start.y)<3)return;
        g.active=true;e.preventDefault();
        try {
            if(['move','resize','rotate'].includes(g.mode)) {
                let delta={x:point.x-g.start.x,y:point.y-g.start.y};
                if(g.mode==='move'&&!e.altKey)delta={x:Math.round(delta.x/8)*8,y:Math.round(delta.y/8)*8};
                const moved=g.mode==='move'?movedSelection(g.selected,delta):null;
                for(const item of g.selected) {
                    const f=item.block.frame;if(!f)continue;let next;
                    if(g.mode==='move')next=moved.get(item.block.id);
                    else if(g.mode==='resize')next=resizedFrame(f,point,item.ancestors,{uniform:e.shiftKey||item.block.type==='group'});
                    else {
                        const inv=inverse(parentMatrix(item.ancestors)),a=transform(inv,g.start),b=transform(inv,point),cx=f.x+f.w/2,cy=f.y+f.h/2;
                        let angle=(f.r||0)+(Math.atan2(b.y-cy,b.x-cx)-Math.atan2(a.y-cy,a.x-cx))*180/Math.PI;
                        if(e.shiftKey)angle=Math.round(angle/15)*15;next={...f,r:angle};
                    }
                    g.frames.set(item.block.id,next);this.previewFrame(item.block.id,next);
                }
            }
            if(g.mode==='flow'){
                g.target=this.pick(point);g.container=null;
                const root=this.bridge.api.slideEl(),destinations=flowDestinations(this.store.model,this.store.ui.slide);
                const cells=root?.querySelectorAll(':scope > .slide-body > .col, :scope > .slide-body > .grid-area')||[];
                cells.forEach((cell,index)=>{const rect=cell.getBoundingClientRect();if(e.clientX>=rect.left&&e.clientX<=rect.right&&e.clientY>=rect.top&&e.clientY<=rect.bottom&&destinations[index])g.container={path:destinations[index].path,rect:bounds([this.bridge.api.toCanvas(rect.left,rect.top),this.bridge.api.toCanvas(rect.right,rect.bottom)])};});
            }
            this.draw();
        }catch(error){this.cancel();this.onError(error);}
    }
    up(e) {
        const g=this.gesture;if(!g)return;this.gesture=null;
        if(!g.active){this.draw();return;}
        try {
            if(g.frames.size)this.store.command(g.mode==='move'?'移动元素':g.mode==='resize'?'调整尺寸':'旋转元素',model=>{
                for(const[id,frame]of g.frames){const item=locate(model,id);if(item)item.block.frame=frame;}
            });
            else if(g.mode==='marquee') {
                const box=bounds([g.start,g.point]),rects=this.bridge.api.rects();
                const ids=Object.entries(rects).filter(([id,rect])=>this.eligible(id)&&overlaps(box,rect)).map(([id])=>id);
                this.store.select(g.shift?[...this.store.ui.selection,...ids]:ids);
            } else if(g.mode==='flow'&&g.target&&g.target!==g.selected[0].block.id) {
                const sourceId=g.selected[0].block.id,targetId=g.target;
                const targetRect=this.bridge.api.rects([targetId])[targetId],after=g.point.y>targetRect.y+targetRect.h/2;
                this.store.command('重排内容',model=>{
                    const source=locate(model,sourceId),target=locate(model,targetId);
                    if(!source||!target||target.block.frame||target.ancestors.some(b=>b.id===sourceId))return;
                    const from=at(model,source.path.slice(0,-1)),to=at(model,target.path.slice(0,-1));
                    if(!Array.isArray(from)||!Array.isArray(to))return;
                    const block=from.splice(source.path.at(-1),1)[0],index=to.findIndex(x=>x.id===targetId);to.splice(index+(after?1:0),0,block);
                });
            } else if(g.mode==='flow'&&g.container){
                this.store.command('移动分栏内容',model=>moveFlow(model,g.selected[0].block.id,g.container.path));
            }
            this.draw();e.preventDefault();
        }catch(error){this.bridge.render(true);this.onError(error);}
    }
    previewFrame(id,f) {
        const el=this.bridge.node(id);if(!el)return;
        Object.assign(el.style,{left:f.x+'px',top:f.y+'px',width:f.w+'px',height:f.h+'px',transform:'rotate('+(f.r||0)+'deg)'});
        const item=locate(this.store.model,id);if(item?.block.type==='group'){
            const inner=el.querySelector(':scope > .ld-group > .ld-group-inner'),n=item.block.natural;
        if(inner&&n)inner.style.transform='scale('+f.w/n.w+','+f.h/n.h+')';
        }
        const scaled=el.querySelector(':scope > .ld-scaled-inner'),natural=item?.block.natural;
        if(scaled&&natural)scaled.style.transform='scale('+f.w/natural.w+','+f.h/natural.h+')';
    }
    enter(e) {
        if(this.store.ui.trial)return;
        const id=this.pick(this.bridge.point(e)),item=id&&locate(this.store.model,id);
        if(item?.block.type==='group'){this.store.ui.groupPath.push(id);this.store.select([]);}
    }
    cancel() { if(!this.gesture)return;this.gesture=null;this.bridge.render(true); }
    draw() {
        const layer=this.bridge.api.layer();if(!layer)return;layer.replaceChildren();
        if(this.store.ui.trial)return;
        const addLine=(points,{closed=true,color='#2563eb',fill='none'}={})=>{
            const svg=this.doc.createElementNS(svgNS,'svg');Object.assign(svg.style,{position:'absolute',inset:'0',width:'100%',height:'100%',overflow:'visible',pointerEvents:'none'});
            const line=this.doc.createElementNS(svgNS,closed?'polygon':'polyline');line.setAttribute('points',points.map(p=>p.x+','+p.y).join(' '));line.setAttribute('fill',fill);line.setAttribute('stroke',color);line.setAttribute('stroke-width','2');line.setAttribute('vector-effect','non-scaling-stroke');svg.append(line);layer.append(svg);
        };
        const handle=(p,id,kind)=>{
            const b=this.doc.createElement('button');b.type='button';b.dataset.ldeHandle=kind;b.dataset.id=id;b.setAttribute('aria-label',kind==='resize'?'调整尺寸':'旋转');
            const size=12/this.bridge.api.geometry().scale;Object.assign(b.style,{position:'absolute',left:(p.x-size/2)+'px',top:(p.y-size/2)+'px',width:size+'px',height:size+'px',padding:'0',border:'2px solid #2563eb',background:'white',borderRadius:kind==='rotate'?'50%':'2px',cursor:kind==='rotate'?'grab':'nwse-resize'});layer.append(b);
        };
        const selected=rootSelection(this.store.model,this.store.ui.selection),rects=this.bridge.api.rects(this.store.ui.selection);
        for(const item of selected){
            const f=this.gesture?.frames.get(item.block.id)||item.block.frame;
            if(f){const points=corners(f,parentMatrix(item.ancestors));addLine(points);
                if(selected.length===1){handle(points[2],item.block.id,'resize');const top={x:(points[0].x+points[1].x)/2,y:(points[0].y+points[1].y)/2},center={x:(points[0].x+points[2].x)/2,y:(points[0].y+points[2].y)/2},len=Math.hypot(top.x-center.x,top.y-center.y)||1;const rotate={x:top.x+(top.x-center.x)/len*28,y:top.y+(top.y-center.y)/len*28};addLine([top,rotate],{closed:false});handle(rotate,item.block.id,'rotate');}
            }else{const r=rects[item.block.id];if(r)addLine([{x:r.x,y:r.y},{x:r.x+r.w,y:r.y},{x:r.x+r.w,y:r.y+r.h},{x:r.x,y:r.y+r.h}]);}
        }
        const g=this.gesture;
        if(g?.active&&g.mode==='marquee'){const b=bounds([g.start,g.point]);addLine([{x:b.x,y:b.y},{x:b.x+b.w,y:b.y},{x:b.x+b.w,y:b.y+b.h},{x:b.x,y:b.y+b.h}],{fill:'#2563eb18'});}
        if(g?.active&&g.mode==='flow'&&g.target){const r=this.bridge.api.rects([g.target])[g.target];if(r){const y=g.point.y>r.y+r.h/2?r.y+r.h:r.y;addLine([{x:r.x,y},{x:r.x+r.w,y}],{closed:false,color:'#059669'});}}
        else if(g?.active&&g.mode==='flow'&&g.container){const r=g.container.rect;addLine([{x:r.x,y:r.y},{x:r.x+r.w,y:r.y},{x:r.x+r.w,y:r.y+r.h},{x:r.x,y:r.y+r.h}],{color:'#059669',fill:'#05966910'});}
    }
    destroy(){this.abort.abort();this.gesture=null;}
}
